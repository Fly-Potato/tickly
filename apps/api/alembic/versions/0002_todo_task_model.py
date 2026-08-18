"""迁移待办任务模型。

Revision ID: 0002_todo_task_model
Revises: 0001_initial_schema
Create Date: 2026-08-17
"""

from collections import defaultdict
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_todo_task_model"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _task_user_orphan_exists() -> bool:
    """以可移植查询检查降级后仍会保留的任务账号外键。"""
    connection = op.get_bind()
    tasks = sa.table("tasks", sa.column("user_id"))
    users = sa.table("users", sa.column("id"))
    return (
        connection.execute(
            sa.select(sa.literal(1))
            .select_from(tasks.outerjoin(users, tasks.c.user_id == users.c.id))
            .where(users.c.id.is_(None))
            .limit(1)
        ).first()
        is not None
    )


def _validate_upgrade_foreign_key_integrity() -> None:
    """在升级的非事务 DDL 前拒绝会让 batch copy 失败的孤儿数据。"""
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # SQLite 可一次检查当前 schema 的全部外键，且此只读 PRAGMA 不改变连接状态。
        violation = connection.exec_driver_sql("PRAGMA foreign_key_check").first()
        if violation is not None:
            raise RuntimeError("外键完整性检查失败，存在历史孤儿数据")
        return

    # 其他 dialect 不使用 SQLite PRAGMA，只检查本 revision 会重建的 tasks 表。
    if _task_user_orphan_exists():
        raise RuntimeError("外键完整性检查失败，tasks 存在历史孤儿数据")


def _validate_downgrade_foreign_key_integrity() -> None:
    """降级前只检查目标旧结构仍保留的 tasks.user_id 外键。"""
    if _task_user_orphan_exists():
        raise RuntimeError("外键完整性检查失败，tasks.user_id 存在历史孤儿数据")


def _backfill_task_values() -> None:
    """按 Python strip 语义回填说明，并规范化状态相关完成时间。

    旧 completed 记录缺少 completed_at 时使用 updated_at；它只是当前可获得的
    最接近历史完成时间，不代表任务真实完成时间。
    """
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, title, notes FROM tasks")
    ).mappings()
    for row in rows:
        notes = row["notes"]
        description = row["title"] if notes is None or not notes.strip() else notes
        connection.execute(
            sa.text(
                "UPDATE tasks SET description = :description WHERE id = :task_id"
            ),
            {"description": description, "task_id": row["id"]},
        )

    connection.execute(
        sa.text(
            "UPDATE tasks SET "
            "migrated_priority = CASE "
            "WHEN priority = 'none' THEN NULL ELSE priority END, "
            "topic = '未分类', "
            "status = CASE WHEN is_completed = 1 THEN 'completed' ELSE 'new' END, "
            "completed_at = CASE "
            "WHEN is_completed = 1 THEN coalesce(completed_at, updated_at) "
            "ELSE NULL END"
        )
    )


def _backfill_serials() -> None:
    """按账号内稳定顺序分配流水号，并推进下一可用计数器。"""
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, user_id FROM tasks "
            "ORDER BY user_id, created_at ASC, id ASC"
        )
    ).mappings()
    counters: dict[str, int] = defaultdict(int)
    for row in rows:
        user_id = row["user_id"]
        counters[user_id] += 1
        connection.execute(
            sa.text("UPDATE tasks SET serial = :serial WHERE id = :task_id"),
            {"serial": counters[user_id], "task_id": row["id"]},
        )

    # 无旧任务账号保留列默认值 1；只推进确实分配过流水号的账号。
    for user_id, last_serial in counters.items():
        connection.execute(
            sa.text(
                "UPDATE users SET next_task_serial = :next_serial "
                "WHERE id = :user_id"
            ),
            {"next_serial": last_serial + 1, "user_id": user_id},
        )


def upgrade() -> None:
    # SQLite 的 ALTER/batch DDL 不能整体回滚，预检必须早于本 revision 的首个 DDL。
    _validate_upgrade_foreign_key_integrity()

    op.add_column(
        "users",
        sa.Column(
            "next_task_serial",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("tasks", sa.Column("serial", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("topic", sa.String(100), nullable=True))
    op.add_column("tasks", sa.Column("status", sa.String(16), nullable=True))
    op.add_column("tasks", sa.Column("parent_id", sa.String(36), nullable=True))
    op.add_column(
        "tasks", sa.Column("migrated_priority", sa.String(16), nullable=True)
    )

    _backfill_task_values()
    _backfill_serials()

    # 旧 priority 同时具有 NOT NULL 和包含 none 的 CHECK，使用临时列在重建时
    # 完成字段切换，避免 batch copy 的来源数据违反旧约束或新约束。SQLite 的
    # 整体 DDL 并非事务原子操作，因此所有可预检的数据完整性问题已在首个 DDL 前阻断。
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_tasks_priority", type_="check")
        batch_op.drop_constraint("ck_tasks_notes_length", type_="check")
        batch_op.drop_index("ix_tasks_user_completed")
        batch_op.alter_column("serial", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column(
            "description", existing_type=sa.Text(), nullable=False
        )
        batch_op.alter_column(
            "topic", existing_type=sa.String(100), nullable=False
        )
        batch_op.alter_column(
            "status", existing_type=sa.String(16), nullable=False
        )
        batch_op.drop_column("notes")
        batch_op.drop_column("is_completed")
        batch_op.drop_column("priority")
        batch_op.alter_column(
            "migrated_priority",
            existing_type=sa.String(16),
            new_column_name="priority",
            nullable=True,
        )
        batch_op.create_unique_constraint(
            "uq_tasks_user_serial", ["user_id", "serial"]
        )
        batch_op.create_check_constraint(
            "ck_tasks_serial_positive", "serial > 0"
        )
        batch_op.create_check_constraint(
            "ck_tasks_description_length",
            "length(description) BETWEEN 1 AND 4000",
        )
        batch_op.create_check_constraint(
            "ck_tasks_topic_length", "length(topic) BETWEEN 1 AND 100"
        )
        batch_op.create_check_constraint(
            "ck_tasks_status",
            "status IN ('new', 'in_progress', 'completed')",
        )
        batch_op.create_check_constraint(
            "ck_tasks_priority",
            "priority IS NULL OR priority IN ('low', 'medium', 'high')",
        )
        # 数据库只保证引用完整性和删除提升；一层父子关系由 service 校验。
        batch_op.create_foreign_key(
            "fk_tasks_parent_id_tasks",
            "tasks",
            ["parent_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_tasks_user_status", ["user_id", "status"])
        batch_op.create_index("ix_tasks_user_topic", ["user_id", "topic"])
        batch_op.create_index("ix_tasks_user_parent", ["user_id", "parent_id"])


def downgrade() -> None:
    # parent_id 将随目标旧结构移除，不应让其异常引用阻断降级；只预检保留的外键。
    _validate_downgrade_foreign_key_integrity()

    op.add_column("tasks", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("is_completed", sa.Boolean(), nullable=True))
    op.add_column(
        "tasks", sa.Column("legacy_priority", sa.String(16), nullable=True)
    )

    connection = op.get_bind()
    connection.execute(sa.text("UPDATE tasks SET notes = description"))
    connection.execute(
        sa.text(
            "UPDATE tasks SET "
            "is_completed = CASE WHEN status = 'completed' THEN 1 ELSE 0 END, "
            "completed_at = CASE "
            "WHEN status = 'completed' THEN completed_at ELSE NULL END"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE tasks SET legacy_priority = CASE "
            "WHEN priority IS NULL THEN 'none' ELSE priority END"
        )
    )

    # 降级有损：in_progress 与 new 都映射为未完成，description 成为 notes；
    # serial、users.next_task_serial、topic、parent_id 及其父子关系会永久丢失。
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "fk_tasks_parent_id_tasks", type_="foreignkey"
        )
        batch_op.drop_constraint("uq_tasks_user_serial", type_="unique")
        batch_op.drop_constraint("ck_tasks_serial_positive", type_="check")
        batch_op.drop_constraint("ck_tasks_description_length", type_="check")
        batch_op.drop_constraint("ck_tasks_topic_length", type_="check")
        batch_op.drop_constraint("ck_tasks_status", type_="check")
        batch_op.drop_constraint("ck_tasks_priority", type_="check")
        batch_op.drop_index("ix_tasks_user_status")
        batch_op.drop_index("ix_tasks_user_topic")
        batch_op.drop_index("ix_tasks_user_parent")
        batch_op.alter_column(
            "is_completed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
        batch_op.drop_column("priority")
        batch_op.alter_column(
            "legacy_priority",
            existing_type=sa.String(16),
            new_column_name="priority",
            nullable=False,
            server_default="none",
        )
        batch_op.drop_column("serial")
        batch_op.drop_column("description")
        batch_op.drop_column("topic")
        batch_op.drop_column("status")
        batch_op.drop_column("parent_id")
        batch_op.create_check_constraint(
            "ck_tasks_notes_length", "notes IS NULL OR length(notes) <= 4000"
        )
        batch_op.create_check_constraint(
            "ck_tasks_priority",
            "priority IN ('none', 'low', 'medium', 'high')",
        )
        batch_op.create_index(
            "ix_tasks_user_completed", ["user_id", "is_completed"]
        )

    op.drop_column("users", "next_task_serial")
