"""为待办增加已废弃状态。

Revision ID: 0003_add_cancelled_task_status
Revises: 0002_todo_task_model
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_add_cancelled_task_status"
down_revision: str | Sequence[str] | None = "0002_todo_task_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_STATUSES = ("new", "in_progress", "completed")
_CURRENT_STATUSES = (*_LEGACY_STATUSES, "cancelled")


def _validate_task_statuses(allowed_statuses: tuple[str, ...]) -> None:
    """在 SQLite 非事务 batch DDL 前拒绝目标约束无法接纳的历史状态。"""
    connection = op.get_bind()
    tasks = sa.table("tasks", sa.column("id"), sa.column("status"))
    invalid_row = connection.execute(
        sa.select(tasks.c.id, tasks.c.status)
        .where(
            sa.or_(
                tasks.c.status.is_(None),
                tasks.c.status.not_in(allowed_statuses),
            )
        )
        .limit(1)
    ).mappings().first()
    if invalid_row is not None:
        raise RuntimeError(
            "检测到未知任务状态，无法安全迁移："
            f"task_id={invalid_row['id']}, status={invalid_row['status']!r}"
        )


def upgrade() -> None:
    # SQLite 重建表的 DDL 无法整体回滚，任何历史坏数据都必须在首个 DDL 前阻断。
    _validate_task_statuses(_LEGACY_STATUSES)

    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_tasks_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tasks_status",
            "status IN ('new', 'in_progress', 'completed', 'cancelled')",
        )


def downgrade() -> None:
    # 先拒绝四态契约之外的坏数据，避免 batch copy 失败后留下部分 DDL。
    _validate_task_statuses(_CURRENT_STATUSES)
    connection = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("status", sa.String(16)),
        sa.column("completed_at", sa.DateTime()),
    )
    # 降级有损：已废弃在旧版本中没有对应状态，只能恢复为未开始并清空完成时间。
    connection.execute(
        sa.update(tasks)
        .where(tasks.c.status == "cancelled")
        .values(status="new", completed_at=None)
    )

    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_tasks_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tasks_status",
            "status IN ('new', 'in_progress', 'completed')",
        )
