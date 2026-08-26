import logging
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
import pytest

from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings
from app.main import create_app


def make_settings(database_path: Path) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=f"sqlite:///{database_path}",
        _env_file=None,
    )


def migrate_to_head(database_path: Path) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")


def test_health_does_not_require_database(tmp_path: Path) -> None:
    unavailable_path = tmp_path / "missing" / "tickly.db"
    app = create_app(make_settings(unavailable_path))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    client.close()


def test_ready_requires_lifespan_before_database_check(tmp_path: Path) -> None:
    unavailable_path = tmp_path / "missing" / "tickly.db"
    app = create_app(make_settings(unavailable_path))
    client = TestClient(app)

    response = client.get("/ready", headers={"X-Request-ID": "not-ready"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "not_ready",
            "message": "服务尚未就绪",
            "request_id": "not-ready",
            "details": [],
        }
    }
    client.close()


def test_ready_accepts_database_at_migration_head(tmp_path: Path) -> None:
    database_path = tmp_path / "current.db"
    migrate_to_head(database_path)
    settings = make_settings(database_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    with TestClient(app) as client:
        assert app.state.ready is True
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    assert app.state.ready is False
    # 注入的 Engine 归调用方所有，应用关闭后仍必须可用。
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    engine.dispose()


def test_ready_rejects_database_without_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.db"
    settings = make_settings(database_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    with TestClient(app) as client:
        response = client.get(
            "/ready",
            headers={"X-Request-ID": "migration-behind"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "migration_not_current",
            "message": "数据库迁移版本不是最新",
            "request_id": "migration-behind",
            "details": [],
        }
    }
    engine.dispose()


def test_ready_reports_unavailable_database_without_logging_protocol_id(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    unavailable_path = tmp_path / "missing" / "tickly.db"
    settings = make_settings(unavailable_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    protocol_request_id = "b" * 64
    caplog.clear()
    root_logger = logging.getLogger()
    readiness_logger = logging.getLogger("tickly.readiness")
    readiness_was_disabled = readiness_logger.disabled
    readiness_logger.disabled = False
    root_logger.addHandler(caplog.handler)
    try:
        with TestClient(app) as client:
            with caplog.at_level(logging.WARNING, logger="tickly.readiness"):
                response = client.get(
                    "/ready",
                    headers={"X-Request-ID": protocol_request_id},
                )
    finally:
        root_logger.removeHandler(caplog.handler)
        readiness_logger.disabled = readiness_was_disabled

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "数据库不可用",
            "request_id": protocol_request_id,
            "details": [],
        }
    }
    assert str(unavailable_path) not in response.text
    readiness_records = [
        record
        for record in caplog.records
        if record.name == "tickly.readiness"
        and record.getMessage() == "readiness.database_unavailable"
    ]
    assert len(readiness_records) == 1
    assert readiness_records[0].request_id != protocol_request_id
    UUID(readiness_records[0].request_id)
    assert protocol_request_id not in caplog.text
    engine.dispose()
