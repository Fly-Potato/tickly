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
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
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
        session.rollback()
        raise
    finally:
        session.close()
