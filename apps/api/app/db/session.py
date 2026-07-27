from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_engine_for_settings(settings: Any, **engine_kwargs: Any) -> Engine:
    connect_args: dict[str, Any] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    database_engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        **engine_kwargs,
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(database_engine, "connect")
        def configure_sqlite_connection(dbapi_connection: Any, _: Any) -> None:
            # SQLite 的连接级开关不会自动继承到新连接，必须在每次建立连接时设置。
            cursor = dbapi_connection.cursor()
            # 外键约束和 WAL 保证数据完整性，并降低读写互相阻塞的概率。
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # busy timeout 给并发写入一个有限等待窗口，超时后由上层转换为可重试错误。
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return database_engine


def create_session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


engine = create_engine_for_settings(Settings())
SessionLocal = create_session_factory(engine)


def get_db_session(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    except Exception:
        # 请求内任意异常都必须回滚，避免未提交的写入污染连接后续请求。
        session.rollback()
        raise
    finally:
        session.close()
