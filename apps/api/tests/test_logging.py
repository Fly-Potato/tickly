import json
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

import app.core.logging as logging_config


def test_alembic_migration_preserves_existing_application_loggers(
    tmp_path: Path,
) -> None:
    """同进程执行 migration 后，应用日志器仍必须可用于后续请求。"""
    access_logger = logging.getLogger("tickly.access")
    was_disabled = access_logger.disabled
    access_logger.disabled = False
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{tmp_path / 'logging-migration.db'}",
    )

    try:
        command.upgrade(config, "head")
        disabled_after_migration = access_logger.disabled
    finally:
        # 即使断言失败也恢复全局日志状态，避免本用例污染后续断言。
        access_logger.disabled = was_disabled

    assert disabled_after_migration is False


def test_json_formatter_emits_access_fields() -> None:
    record = logging.LogRecord(name="tickly.access", level=logging.INFO, pathname=__file__, lineno=10, msg="request.completed", args=(), exc_info=None)
    record.request_id = "json-log"
    record.method = "GET"
    record.path = "/health"
    record.status = 200
    record.duration_ms = 1.25
    payload = json.loads(logging_config.JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "request.completed"
    assert payload["request_id"] == "json-log"
    assert payload["method"] == "GET"
    assert payload["path"] == "/health"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.25
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_whitelists_safe_account_context() -> None:
    record = logging.LogRecord(
        name="tickly.account",
        level=logging.ERROR,
        pathname=__file__,
        lineno=30,
        msg="account.password_change.failed",
        args=(),
        exc_info=None,
    )
    record.request_id = "safe-request"
    record.user_id = "safe-user"
    record.stage = "database"
    record.password = "password-secret-sentinel"
    record.sql = "SELECT sql-secret-sentinel"
    record.params = {"secret": "params-secret-sentinel"}

    rendered = logging_config.JsonFormatter().format(record)
    payload = json.loads(rendered)

    assert payload["request_id"] == "safe-request"
    assert payload["user_id"] == "safe-user"
    assert payload["stage"] == "database"
    assert "exception" not in payload
    assert "password" not in payload
    assert "sql" not in payload
    assert "params" not in payload
    for sentinel in (
        "password-secret-sentinel",
        "sql-secret-sentinel",
        "params-secret-sentinel",
    ):
        assert sentinel not in rendered


def test_plain_formatter_appends_only_present_safe_context() -> None:
    record = logging.LogRecord(
        name="tickly.account",
        level=logging.ERROR,
        pathname=__file__,
        lineno=60,
        msg="account.password_change.failed",
        args=(),
        exc_info=None,
    )
    record.request_id = "plain-request"
    record.user_id = "plain-user"
    record.stage = "password_hash"
    record.password = "plain-password-secret"
    record.sql = "plain-sql-secret"

    formatter = logging_config.PlainTextFormatter(
        "%(levelname)s %(name)s %(message)s"
    )
    rendered = formatter.format(record)

    assert "request_id=plain-request" in rendered
    assert "user_id=plain-user" in rendered
    assert "stage=password_hash" in rendered
    assert "method=" not in rendered
    assert "path=" not in rendered
    assert "plain-password-secret" not in rendered
    assert "plain-sql-secret" not in rendered
