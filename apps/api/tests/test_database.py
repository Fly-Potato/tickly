from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.db.session import (
    create_engine_for_settings,
    create_session_factory,
    get_db_session,
)


def make_settings(database_path: Path) -> SimpleNamespace:
    return SimpleNamespace(database_url=f"sqlite:///{database_path}")


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
    dependency = get_db_session(session_factory)
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
