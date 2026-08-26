from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import AuthSession, McpToken, Task, User


def make_session_factory(tmp_path: Path):
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{tmp_path / 'models.db'}"})()
    )
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def test_models_expose_required_tables_and_task_indexes(tmp_path: Path) -> None:
    engine, _ = make_session_factory(tmp_path)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    user_columns = {
        column["name"]: column for column in inspector.get_columns("users")
    }
    task_columns = {
        column["name"]: column for column in inspector.get_columns("tasks")
    }
    task_checks = {
        constraint["name"]: " ".join(constraint["sqltext"].split())
        for constraint in inspector.get_check_constraints("tasks")
    }
    task_indexes = {
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("tasks")
    }
    task_uniques = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints("tasks")
    }
    task_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("tasks")
    }

    assert table_names == {"users", "auth_sessions", "tasks", "mcp_tokens"}
    assert user_columns["next_task_serial"]["nullable"] is False
    assert (
        str(user_columns["next_task_serial"]["default"]).strip("'\"()") == "1"
    )
    assert {
        "serial",
        "description",
        "priority",
        "topic",
        "status",
        "parent_id",
    } <= task_columns.keys()
    assert task_columns["serial"]["nullable"] is False
    assert task_columns["description"]["nullable"] is False
    assert task_columns["priority"]["nullable"] is True
    assert task_columns["topic"]["nullable"] is False
    assert task_columns["status"]["nullable"] is False
    assert task_columns["parent_id"]["nullable"] is True
    assert task_checks == {
        "ck_tasks_title_length": "length(title) BETWEEN 1 AND 200",
        "ck_tasks_serial_positive": "serial > 0",
        "ck_tasks_description_length": "length(description) BETWEEN 1 AND 4000",
        "ck_tasks_topic_length": "length(topic) BETWEEN 1 AND 100",
        "ck_tasks_status": (
            "status IN ('new', 'in_progress', 'completed', 'cancelled')"
        ),
        "ck_tasks_priority": (
            "priority IS NULL OR priority IN ('low', 'medium', 'high')"
        ),
    }
    assert task_indexes == {
        "ix_tasks_user_status": ["user_id", "status"],
        "ix_tasks_user_topic": ["user_id", "topic"],
        "ix_tasks_user_parent": ["user_id", "parent_id"],
        "ix_tasks_user_due": ["user_id", "due_at"],
        "ix_tasks_user_created": ["user_id", "created_at"],
    }
    assert task_uniques["uq_tasks_user_serial"] == ["user_id", "serial"]
    parent_foreign_key = task_foreign_keys[("parent_id",)]
    assert parent_foreign_key["referred_table"] == "tasks"
    assert parent_foreign_key["referred_columns"] == ["id"]
    assert parent_foreign_key["options"]["ondelete"] == "SET NULL"
    engine.dispose()


def test_mcp_token_model_contract_and_user_delete_cascade(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    inspector = inspect(engine)
    user_columns = {
        column["name"]: column for column in inspector.get_columns("users")
    }
    token_columns = {
        column["name"]: column
        for column in inspector.get_columns("mcp_tokens")
    }
    token_checks = {
        constraint["name"]: " ".join(constraint["sqltext"].split())
        for constraint in inspector.get_check_constraints("mcp_tokens")
    }
    token_uniques = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints("mcp_tokens")
    }
    token_indexes = {
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("mcp_tokens")
    }
    token_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("mcp_tokens")
    }

    assert user_columns["auth_version"]["nullable"] is False
    assert str(user_columns["auth_version"]["default"]).strip("'\"()") == "1"
    assert set(token_columns) == {
        "id",
        "user_id",
        "name",
        "token_hash",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "created_at",
    }
    for column_name in ("id", "user_id", "name", "token_hash", "created_at"):
        assert token_columns[column_name]["nullable"] is False
    for column_name in ("expires_at", "revoked_at", "last_used_at"):
        assert token_columns[column_name]["nullable"] is True
    assert token_checks == {
        "ck_mcp_tokens_name_length": (
            "instr(name, char(0)) = 0 AND length(name) BETWEEN 1 AND 64"
        ),
        "ck_mcp_tokens_hash_format": (
            "instr(token_hash, char(0)) = 0 AND length(token_hash) = 64 "
            "AND token_hash = lower(token_hash) AND token_hash NOT GLOB '*[^0-9a-f]*'"
        ),
    }
    assert token_uniques == {"uq_mcp_tokens_token_hash": ["token_hash"]}
    assert token_indexes == {"ix_mcp_tokens_user_id": ["user_id"]}
    user_foreign_key = token_foreign_keys[("user_id",)]
    assert user_foreign_key["referred_table"] == "users"
    assert user_foreign_key["referred_columns"] == ["id"]
    assert user_foreign_key["options"]["ondelete"] == "CASCADE"

    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        user.mcp_tokens.append(McpToken(name="开发环境", token_hash="a" * 64))
        session.add(user)
        session.commit()
        user_id = user.id

    with engine.begin() as connection:
        # 使用原生 SQL 触发数据库外键，避免 ORM delete-orphan 掩盖约束缺失。
        connection.exec_driver_sql("DELETE FROM users WHERE id = ?", (user_id,))

    with session_factory() as session:
        assert session.query(McpToken).filter_by(user_id=user_id).count() == 0

    engine.dispose()


@pytest.mark.parametrize(
    ("name", "token_hash"),
    [
        ("\0开头", "a" * 64),
        ("中间\0字符", "b" * 64),
        ("名" * 64 + "\0", "c" * 64),
        ("有效名称", "\0" + "d" * 64),
        ("有效名称", "e" * 32 + "\0" + "e" * 32),
        ("有效名称", "f" * 64 + "\0"),
    ],
    ids=[
        "名称开头不允许空字符",
        "名称中间不允许空字符",
        "名称第六十四字符后不允许空字符",
        "摘要开头不允许空字符",
        "摘要中间不允许空字符",
        "摘要第六十四字符后不允许空字符",
    ],
)
def test_mcp_token_constraints_reject_embedded_nul(
    tmp_path: Path,
    name: str,
    token_hash: str,
) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        user.mcp_tokens.append(McpToken(name=name, token_hash=token_hash))
        session.add(user)
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()


def test_username_is_unique(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        session.add_all(
            [
                User(username="person", password_hash="hash"),
                User(username="person", password_hash="hash2"),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


def test_username_constraints_reject_non_normalized_values(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    invalid_usernames = ["ab", "A_user", "has space", "中文名", "a" * 33]

    for index, username in enumerate(invalid_usernames):
        with session_factory() as session:
            session.add(User(username=username, password_hash=f"hash-{index}"))
            with pytest.raises(IntegrityError):
                session.commit()

    engine.dispose()


def test_deleting_user_cascades_auth_session_and_tasks(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        user.auth_sessions.append(
            AuthSession(
                refresh_token_hash="refresh-hash",
                expires_at=datetime.now(timezone.utc),
                last_used_at=datetime.now(timezone.utc),
            )
        )
        user.tasks.append(
            Task(
                serial=1,
                title="A task",
                description="A task",
                topic="未分类",
            )
        )
        session.add(user)
        session.commit()
        user_id = user.id

        session.delete(user)
        session.commit()

        assert session.query(AuthSession).filter_by(user_id=user_id).count() == 0
        assert session.query(Task).filter_by(user_id=user_id).count() == 0
    engine.dispose()


def test_task_defaults(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        session.add(user)
        session.flush()
        task = Task(
            user_id=user.id,
            serial=1,
            title="Inbox item",
            description="Inbox item",
            topic="未分类",
        )
        session.add(task)
        session.commit()

        assert user.next_task_serial == 1
        assert task.status == "new"
        assert task.priority is None
        assert task.created_at.tzinfo is not None
        assert task.updated_at.tzinfo is not None

    engine.dispose()


def test_task_length_constraints_accept_maximum_values(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        session.add(user)
        session.flush()
        session.add(
            Task(
                user_id=user.id,
                serial=1,
                title="题" * 200,
                description="说" * 4000,
                topic="类" * 100,
            )
        )
        session.commit()

        assert session.query(Task).count() == 1

    engine.dispose()


def test_task_status_constraint_accepts_cancelled(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        session.add(user)
        session.flush()
        session.add(
            Task(
                user_id=user.id,
                serial=1,
                title="已废弃任务",
                description="保留记录但不再执行",
                topic="Tickly",
                status="cancelled",
            )
        )
        session.commit()

        assert session.query(Task).one().status == "cancelled"

    engine.dispose()


@pytest.mark.parametrize(
    ("serial", "title", "description", "topic", "extra_fields"),
    [
        (0, "流水号非法", "流水号必须为正整数", "未分类", {}),
        (1, "", "标题不能为空", "未分类", {}),
        (1, "题" * 201, "标题不能超长", "未分类", {}),
        (1, "说明为空", "", "未分类", {}),
        (1, "说明超长", "说" * 4001, "未分类", {}),
        (1, "主题为空", "主题不能为空", "", {}),
        (1, "主题超长", "主题不能超长", "类" * 101, {}),
        (1, "状态非法", "状态必须属于约定集合", "未分类", {"status": "invalid"}),
        (1, "优先级非法", "优先级必须属于约定集合或为空", "未分类", {"priority": "urgent"}),
    ],
    ids=[
        "流水号必须为正整数",
        "标题不能为空",
        "标题不能超过最大长度",
        "说明不能为空",
        "说明不能超过最大长度",
        "主题不能为空",
        "主题不能超过最大长度",
        "状态必须属于约定集合",
        "优先级必须属于约定集合或为空",
    ],
)
def test_task_check_constraints_reject_invalid_values(
    tmp_path: Path,
    serial: int,
    title: str,
    description: str,
    topic: str,
    extra_fields: dict[str, str],
) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        session.add(user)
        session.flush()
        session.add(
            Task(
                user_id=user.id,
                serial=serial,
                title=title,
                description=description,
                topic=topic,
                **extra_fields,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()


def test_task_serial_is_unique_within_each_user(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        first_user = User(username="first", password_hash="hash")
        second_user = User(username="second", password_hash="hash")
        session.add_all([first_user, second_user])
        session.flush()
        session.add_all(
            [
                Task(
                    user_id=first_user.id,
                    serial=1,
                    title="账号一任务",
                    description="账号一任务",
                    topic="未分类",
                ),
                Task(
                    user_id=second_user.id,
                    serial=1,
                    title="账号二任务",
                    description="账号二任务",
                    topic="未分类",
                ),
            ]
        )
        session.commit()

        session.add(
            Task(
                user_id=first_user.id,
                serial=1,
                title="账号一重复流水号",
                description="同一账号不能复用流水号",
                topic="未分类",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()


def test_deleting_parent_promotes_children_to_roots(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        session.add(user)
        session.flush()
        parent = Task(
            user_id=user.id,
            serial=1,
            title="父任务",
            description="父任务",
            topic="Tickly",
        )
        child = Task(
            user_id=user.id,
            serial=2,
            title="子任务",
            description="子任务",
            topic="Tickly",
            parent=parent,
        )
        session.add_all([parent, child])
        session.commit()
        parent_id = parent.id
        child_id = child.id

    with session_factory() as session:
        parent = session.get(Task, parent_id)
        assert parent is not None
        # 新 Session 未加载 children，删除行为只能依赖数据库 ON DELETE SET NULL。
        assert "children" in inspect(parent).unloaded
        session.delete(parent)
        session.commit()

    with session_factory() as session:
        promoted = session.get(Task, child_id)
        assert promoted is not None
        assert promoted.parent_id is None
    engine.dispose()
