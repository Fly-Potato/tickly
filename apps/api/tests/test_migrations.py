from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core.config import API_ROOT
from app.db.session import create_engine_for_settings


def test_initial_migration_can_upgrade_and_downgrade_file_database(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    # 使用真实临时文件验证 migration，不把内存数据库当作唯一集成环境。
    command.upgrade(config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{database_path}"})()
    )
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "alembic_version",
        "users",
        "auth_sessions",
        "tasks",
    }
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    user_checks = {
        check["name"] for check in inspector.get_check_constraints("users")
    }
    assert "username" in user_columns
    assert "email" not in user_columns
    assert {
        "ck_users_username_length",
        "ck_users_username_format",
    } <= user_checks

    command.downgrade(config, "base")
    # Alembic 自己的版本表保留，但业务表必须全部回退。
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_migration_uses_database_url_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "environment.db"
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "TICKLY_DATABASE_URL",
        f"sqlite:///{database_path}",
    )
    config = Config(str(API_ROOT / "alembic.ini"))

    # 容器 migration 必须服从环境配置，不能写入镜像内的开发默认路径。
    command.upgrade(config, "head")

    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{database_path}"})()
    )
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "auth_sessions",
        "tasks",
    }
    engine.dispose()


def test_task_model_migration_backfills_schema_and_can_downgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "task-model-migration.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001_initial_schema")

    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01'), "
            "('u2', 'other', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, title, notes, is_completed, priority, due_at, "
            "completed_at, created_at, updated_at) VALUES "
            "('t1', 'u1', '第一项', NULL, 0, 'none', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 09:00:00'), "
            "('t2', 'u1', '第二项', '详细说明', 1, 'high', NULL, "
            "'2026-08-02 10:00:00', '2026-08-02 09:00:00', "
            "'2026-08-02 10:00:00'), "
            "('t3', 'u1', '第三项', '   ', 1, 'low', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 11:00:00'), "
            "('t4', 'u2', '其他账号', NULL, 0, 'medium', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 09:00:00')"
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT id, serial, description, priority, topic, status, "
            "completed_at, parent_id FROM tasks ORDER BY user_id, serial"
        ).mappings().all()
        counters = connection.exec_driver_sql(
            "SELECT id, next_task_serial FROM users ORDER BY id"
        ).all()

    assert [dict(row) for row in rows] == [
        {
            "id": "t1",
            "serial": 1,
            "description": "第一项",
            "priority": None,
            "topic": "未分类",
            "status": "new",
            "completed_at": None,
            "parent_id": None,
        },
        {
            "id": "t2",
            "serial": 2,
            "description": "详细说明",
            "priority": "high",
            "topic": "未分类",
            "status": "completed",
            "completed_at": "2026-08-02 10:00:00",
            "parent_id": None,
        },
        {
            "id": "t3",
            "serial": 3,
            "description": "第三项",
            "priority": "low",
            "topic": "未分类",
            "status": "completed",
            "completed_at": "2026-08-02 11:00:00",
            "parent_id": None,
        },
        {
            "id": "t4",
            "serial": 1,
            "description": "其他账号",
            "priority": "medium",
            "topic": "未分类",
            "status": "new",
            "completed_at": None,
            "parent_id": None,
        },
    ]
    assert counters == [("u1", 4), ("u2", 2)]

    upgraded_inspector = inspect(engine)
    user_columns = {
        column["name"]: column for column in upgraded_inspector.get_columns("users")
    }
    task_columns = {
        column["name"]: column for column in upgraded_inspector.get_columns("tasks")
    }
    task_checks = {
        check["name"]: check["sqltext"]
        for check in upgraded_inspector.get_check_constraints("tasks")
    }
    task_indexes = {
        index["name"] for index in upgraded_inspector.get_indexes("tasks")
    }
    task_uniques = {
        constraint["name"]: constraint["column_names"]
        for constraint in upgraded_inspector.get_unique_constraints("tasks")
    }
    task_foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in upgraded_inspector.get_foreign_keys("tasks")
    }
    assert user_columns["next_task_serial"]["nullable"] is False
    assert str(user_columns["next_task_serial"]["default"]).strip("'\"") == "1"
    assert {
        "id",
        "user_id",
        "serial",
        "title",
        "description",
        "priority",
        "topic",
        "status",
        "due_at",
        "completed_at",
        "parent_id",
        "created_at",
        "updated_at",
    } == set(task_columns)
    for column_name in ("serial", "description", "topic", "status"):
        assert task_columns[column_name]["nullable"] is False
    assert task_columns["priority"]["nullable"] is True
    assert {
        "ck_tasks_title_length",
        "ck_tasks_serial_positive",
        "ck_tasks_description_length",
        "ck_tasks_topic_length",
        "ck_tasks_status",
        "ck_tasks_priority",
    } == set(task_checks)
    assert "serial > 0" in task_checks["ck_tasks_serial_positive"]
    assert "length(description) BETWEEN 1 AND 4000" in task_checks[
        "ck_tasks_description_length"
    ]
    assert "length(topic) BETWEEN 1 AND 100" in task_checks[
        "ck_tasks_topic_length"
    ]
    assert "'new', 'in_progress', 'completed'" in task_checks["ck_tasks_status"]
    assert "'low', 'medium', 'high'" in task_checks["ck_tasks_priority"]
    assert task_uniques["uq_tasks_user_serial"] == ["user_id", "serial"]
    assert {
        "ix_tasks_user_due",
        "ix_tasks_user_created",
        "ix_tasks_user_status",
        "ix_tasks_user_topic",
        "ix_tasks_user_parent",
    } == task_indexes
    assert task_foreign_keys["fk_tasks_parent_id_tasks"]["referred_table"] == "tasks"
    assert task_foreign_keys["fk_tasks_parent_id_tasks"]["constrained_columns"] == [
        "parent_id"
    ]
    assert task_foreign_keys["fk_tasks_parent_id_tasks"]["options"] == {
        "ondelete": "SET NULL"
    }

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u3', 'empty', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
        empty_user_counter = connection.exec_driver_sql(
            "SELECT next_task_serial FROM users WHERE id = 'u3'"
        ).scalar_one()
    assert empty_user_counter == 1

    with engine.begin() as connection:
        # 降级必须把进行中状态有损映射为旧模型的未完成状态。
        connection.exec_driver_sql(
            "UPDATE tasks SET status = 'in_progress' WHERE id = 't4'"
        )

    command.downgrade(config, "0001_initial_schema")

    with engine.connect() as connection:
        downgraded = connection.exec_driver_sql(
            "SELECT id, notes, is_completed, priority FROM tasks ORDER BY id"
        ).mappings().all()
    assert [dict(row) for row in downgraded] == [
        {"id": "t1", "notes": "第一项", "is_completed": 0, "priority": "none"},
        {
            "id": "t2",
            "notes": "详细说明",
            "is_completed": 1,
            "priority": "high",
        },
        {"id": "t3", "notes": "第三项", "is_completed": 1, "priority": "low"},
        {
            "id": "t4",
            "notes": "其他账号",
            "is_completed": 0,
            "priority": "medium",
        },
    ]
    downgraded_inspector = inspect(engine)
    downgraded_user_columns = {
        column["name"]: column
        for column in downgraded_inspector.get_columns("users")
    }
    downgraded_task_columns = {
        column["name"]: column
        for column in downgraded_inspector.get_columns("tasks")
    }
    assert "next_task_serial" not in {
        column["name"] for column in downgraded_user_columns.values()
    }
    assert {
        "id",
        "user_id",
        "title",
        "notes",
        "is_completed",
        "priority",
        "due_at",
        "completed_at",
        "created_at",
        "updated_at",
    } == set(downgraded_task_columns)
    assert downgraded_task_columns["is_completed"]["nullable"] is False
    assert str(downgraded_task_columns["is_completed"]["default"]).strip("'\"") in {
        "0",
        "false",
    }
    assert downgraded_task_columns["priority"]["nullable"] is False
    assert (
        str(downgraded_task_columns["priority"]["default"]).strip("'\"")
        == "none"
    )
    assert {
        "ck_tasks_title_length",
        "ck_tasks_notes_length",
        "ck_tasks_priority",
    } == {
        check["name"]
        for check in downgraded_inspector.get_check_constraints("tasks")
    }
    assert {
        "ix_tasks_user_completed",
        "ix_tasks_user_due",
        "ix_tasks_user_created",
    } == {
        index["name"] for index in downgraded_inspector.get_indexes("tasks")
    }
    engine.dispose()


def test_task_model_migration_normalizes_whitespace_and_completion_time(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "task-model-normalization.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001_initial_schema")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, title, notes, is_completed, priority, due_at, "
            "completed_at, created_at, updated_at) VALUES "
            "(?, 'u1', ?, ?, ?, 'none', NULL, ?, ?, ?)",
            [
                (
                    "empty",
                    "空字符串备注",
                    "",
                    0,
                    None,
                    "2026-08-02 09:00:00",
                    "2026-08-02 09:00:00",
                ),
                (
                    "ascii-whitespace",
                    "控制空白备注",
                    "\t\r\n\f\v",
                    0,
                    None,
                    "2026-08-02 10:00:00",
                    "2026-08-02 10:00:00",
                ),
                (
                    "stale-completion",
                    "未完成残留时间",
                    "保留的说明",
                    0,
                    "2026-08-01 08:00:00",
                    "2026-08-02 11:00:00",
                    "2026-08-02 11:00:00",
                ),
            ],
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        upgraded = connection.exec_driver_sql(
            "SELECT id, description, status, completed_at FROM tasks ORDER BY id"
        ).mappings().all()
    assert [dict(row) for row in upgraded] == [
        {
            "id": "ascii-whitespace",
            "description": "控制空白备注",
            "status": "new",
            "completed_at": None,
        },
        {
            "id": "empty",
            "description": "空字符串备注",
            "status": "new",
            "completed_at": None,
        },
        {
            "id": "stale-completion",
            "description": "保留的说明",
            "status": "new",
            "completed_at": None,
        },
    ]

    with engine.begin() as connection:
        # 即使新版数据异常残留时间，降级后的未完成任务也必须清空完成时间。
        connection.exec_driver_sql(
            "UPDATE tasks SET status = 'in_progress', "
            "completed_at = '2026-08-03 12:00:00' "
            "WHERE id = 'ascii-whitespace'"
        )
    command.downgrade(config, "0001_initial_schema")
    with engine.connect() as connection:
        downgraded = connection.exec_driver_sql(
            "SELECT id, is_completed, completed_at FROM tasks ORDER BY id"
        ).mappings().all()
    assert [dict(row) for row in downgraded] == [
        {"id": "ascii-whitespace", "is_completed": 0, "completed_at": None},
        {"id": "empty", "is_completed": 0, "completed_at": None},
        {"id": "stale-completion", "is_completed": 0, "completed_at": None},
    ]
    engine.dispose()


def test_task_model_migration_rejects_orphans_before_ddl_and_can_retry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "task-model-orphan.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001_initial_schema")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )

    with engine.connect() as connection:
        # 仅为构造历史坏数据临时关闭连接级外键检查，写入后立即恢复。
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, title, notes, is_completed, priority, due_at, "
            "completed_at, created_at, updated_at) VALUES "
            "('orphan', 'missing-user', '孤儿任务', NULL, 0, 'none', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 09:00:00')"
        )
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1

    with pytest.raises(RuntimeError, match="外键完整性"):
        command.upgrade(config, "head")

    failed_inspector = inspect(engine)
    assert {
        "id",
        "user_id",
        "title",
        "notes",
        "is_completed",
        "priority",
        "due_at",
        "completed_at",
        "created_at",
        "updated_at",
    } == {column["name"] for column in failed_inspector.get_columns("tasks")}
    assert "next_task_serial" not in {
        column["name"] for column in failed_inspector.get_columns("users")
    }
    assert "_alembic_tmp_tasks" not in failed_inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0001_initial_schema"

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("DELETE FROM tasks WHERE id = 'orphan'")
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0002_todo_task_model"
    assert "serial" in {
        column["name"] for column in inspect(engine).get_columns("tasks")
    }
    engine.dispose()


def test_task_model_migration_enforces_constraints_and_foreign_key_actions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "task-model-constraints.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, serial, title, description, priority, topic, status, "
            "due_at, completed_at, parent_id, created_at, updated_at) VALUES "
            "('parent', 'u1', 1, '父任务', '父任务', NULL, '未分类', 'new', "
            "NULL, NULL, NULL, '2026-08-02', '2026-08-02'), "
            "('child', 'u1', 2, '子任务', '子任务', 'low', '未分类', 'in_progress', "
            "NULL, NULL, 'parent', '2026-08-02', '2026-08-02')"
        )

    invalid_values = [
        ("duplicate", 1, "new", None),
        ("invalid-status", 3, "blocked", None),
        ("invalid-priority", 4, "new", "urgent"),
    ]
    for task_id, serial, status, priority in invalid_values:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO tasks "
                    "(id, user_id, serial, title, description, priority, topic, "
                    "status, due_at, completed_at, parent_id, created_at, updated_at) "
                    "VALUES (?, 'u1', ?, '非法任务', '非法任务', ?, '未分类', ?, "
                    "NULL, NULL, NULL, '2026-08-02', '2026-08-02')",
                    (task_id, serial, priority, status),
                )

    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM tasks WHERE id = 'parent'")
        promoted_child = connection.exec_driver_sql(
            "SELECT id, parent_id FROM tasks WHERE id = 'child'"
        ).one()
        assert promoted_child == ("child", None)
        connection.exec_driver_sql("DELETE FROM users WHERE id = 'u1'")
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM tasks"
        ).scalar_one() == 0

    command.downgrade(config, "0001_initial_schema")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u2', 'legacy', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, title, notes, is_completed, priority, due_at, "
            "completed_at, created_at, updated_at) VALUES "
            "('legacy', 'u2', '旧任务', NULL, 0, 'none', NULL, NULL, "
            "'2026-08-02', '2026-08-02')"
        )
        connection.exec_driver_sql("DELETE FROM users WHERE id = 'u2'")
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM tasks"
        ).scalar_one() == 0
    engine.dispose()


def test_task_model_downgrade_rejects_user_orphans_before_ddl_and_can_retry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "task-model-downgrade-orphan.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
    with engine.connect() as connection:
        # 同时构造保留外键和即将移除外键的坏数据，验证降级只阻断前者。
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, serial, title, description, priority, topic, status, "
            "due_at, completed_at, parent_id, created_at, updated_at) VALUES "
            "('user-orphan', 'missing-user', 1, '账号孤儿', '账号孤儿', NULL, "
            "'未分类', 'new', NULL, NULL, NULL, '2026-08-02', '2026-08-02'), "
            "('parent-orphan', 'u1', 1, '父级孤儿', '父级孤儿', NULL, "
            "'未分类', 'new', NULL, NULL, 'missing-parent', "
            "'2026-08-02', '2026-08-02')"
        )
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1

    with pytest.raises(RuntimeError, match="tasks.user_id"):
        command.downgrade(config, "0001_initial_schema")

    failed_inspector = inspect(engine)
    assert {
        "id",
        "user_id",
        "serial",
        "title",
        "description",
        "priority",
        "topic",
        "status",
        "due_at",
        "completed_at",
        "parent_id",
        "created_at",
        "updated_at",
    } == {column["name"] for column in failed_inspector.get_columns("tasks")}
    assert "_alembic_tmp_tasks" not in failed_inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0002_todo_task_model"

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("DELETE FROM tasks WHERE id = 'user-orphan'")
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    command.downgrade(config, "0001_initial_schema")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0001_initial_schema"
        downgraded = connection.exec_driver_sql(
            "SELECT id, notes, is_completed FROM tasks"
        ).one()
    assert downgraded == ("parent-orphan", "父级孤儿", 0)
    engine.dispose()
