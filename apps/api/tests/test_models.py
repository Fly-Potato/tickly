from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import AuthSession, Task, User


def make_session_factory(tmp_path: Path):
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{tmp_path / 'models.db'}"})()
    )
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def test_models_expose_required_tables_and_task_indexes(tmp_path: Path) -> None:
    engine, _ = make_session_factory(tmp_path)
    table_names = set(inspect(engine).get_table_names())
    task_indexes = {index["name"] for index in inspect(engine).get_indexes("tasks")}

    assert table_names == {"users", "auth_sessions", "tasks"}
    assert {"ix_tasks_user_completed", "ix_tasks_user_due", "ix_tasks_user_created"} <= task_indexes
    engine.dispose()


def test_user_email_is_unique(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        session.add_all([User(email="person@example.com", password_hash="hash"), User(email="person@example.com", password_hash="hash2")])
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


def test_deleting_user_cascades_auth_session_and_tasks(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(email="person@example.com", password_hash="hash")
        user.auth_sessions.append(
            AuthSession(
                refresh_token_hash="refresh-hash",
                expires_at=datetime.now(timezone.utc),
                last_used_at=datetime.now(timezone.utc),
            )
        )
        user.tasks.append(Task(title="A task"))
        session.add(user)
        session.commit()
        user_id = user.id

        session.delete(user)
        session.commit()

        assert session.query(AuthSession).filter_by(user_id=user_id).count() == 0
        assert session.query(Task).filter_by(user_id=user_id).count() == 0
    engine.dispose()


def test_task_defaults_and_constraints(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(email="person@example.com", password_hash="hash")
        session.add(user)
        session.flush()
        task = Task(user_id=user.id, title="Inbox item")
        session.add(task)
        session.commit()

        assert task.is_completed is False
        assert task.priority == "none"
        assert task.created_at.tzinfo is not None
        assert task.updated_at.tzinfo is not None

        session.add(Task(user_id=user.id, title="", priority="none"))
        with pytest.raises(IntegrityError):
            session.commit()
            
    engine.dispose()
