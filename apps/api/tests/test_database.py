from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.dependencies import DbSession, get_db_session
from app.core.config import Environment, Settings
from app.db.session import (
    create_engine_for_settings,
    create_session_factory,
)
from app.main import create_app
from app.models import Task, User


def make_settings(database_path: Path) -> SimpleNamespace:
    return SimpleNamespace(database_url=f"sqlite:///{database_path}")


def migrate_to_head(database_path: Path) -> None:
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(alembic_config, "head")


def test_sqlite_engine_enables_required_pragmas(tmp_path: Path) -> None:
    engine = create_engine_for_settings(make_settings(tmp_path / "tickly.db"))

    with engine.connect() as connection:
        # 这些连接级设置是生产 SQLite 数据完整性和并发行为的基础。
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA busy_timeout")) > 0

    engine.dispose()


def test_session_dependency_rolls_back_when_request_fails(tmp_path: Path) -> None:
    engine = create_engine_for_settings(make_settings(tmp_path / "tickly.db"))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE entries (value TEXT NOT NULL)"))

    session_factory = create_session_factory(engine)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(database_session_factory=session_factory)
        )
    )
    dependency = get_db_session(request)  # type: ignore[arg-type]
    session = next(dependency)
    session.execute(text("INSERT INTO entries (value) VALUES ('uncommitted')"))

    try:
        # 模拟请求处理失败，验证依赖退出路径会撤销本次事务。
        dependency.throw(RuntimeError("request failed"))
    except RuntimeError:
        pass

    with session_factory() as verification_session:
        count = verification_session.scalar(text("SELECT count(*) FROM entries"))

    assert count == 0
    engine.dispose()


def test_request_session_uses_application_engine(tmp_path: Path) -> None:
    database_path = tmp_path / "request.db"
    migrate_to_head(database_path)
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite:///{database_path}",
        _env_file=None,
    )
    engine = create_engine_for_settings(settings)
    application = create_app(settings, database_engine=engine)

    @application.post("/session-marker")
    def create_marker(session: DbSession) -> dict[str, str]:
        session.add(User(username="marker", password_hash="hash"))
        session.commit()
        return {"status": "created"}

    with TestClient(application) as client:
        assert client.post("/session-marker").status_code == 200

    with create_session_factory(engine)() as session:
        assert session.scalar(select(User.username)) == "marker"

    engine.dispose()


def test_file_database_persists_after_engine_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent.db"
    migrate_to_head(database_path)

    first_engine = create_engine_for_settings(make_settings(database_path))
    first_session_factory = create_session_factory(first_engine)
    with first_session_factory() as session:
        user = User(username="persistent", password_hash="hash")
        user.tasks.append(Task(title="跨重启保留的任务", priority="high"))
        session.add(user)
        session.commit()
        user_id = user.id
        task_id = user.tasks[0].id

    # 释放并重新创建 Engine，模拟 API 进程重启后只依赖同一个 SQLite 文件恢复状态。
    first_engine.dispose()
    restarted_engine = create_engine_for_settings(make_settings(database_path))
    restarted_session_factory = create_session_factory(restarted_engine)

    with restarted_session_factory() as session:
        persisted_user = session.scalar(select(User).where(User.id == user_id))
        persisted_task = session.scalar(select(Task).where(Task.id == task_id))

        assert persisted_user is not None
        assert persisted_user.username == "persistent"
        assert persisted_task is not None
        assert persisted_task.user_id == user_id
        assert persisted_task.title == "跨重启保留的任务"
        assert persisted_task.priority == "high"

    restarted_engine.dispose()
