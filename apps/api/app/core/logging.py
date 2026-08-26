from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any

from app.core.config import Settings


ACCESS_FIELDS = ("request_id", "method", "path", "status", "duration_ms")
SAFE_CONTEXT_FIELDS = ACCESS_FIELDS + ("user_id", "stage")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for field in SAFE_CONTEXT_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class PlainTextFormatter(logging.Formatter):
    """在普通日志末尾追加显式白名单中的安全上下文字段。"""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        context = " ".join(
            f"{field}={getattr(record, field)}"
            for field in SAFE_CONTEXT_FIELDS
            if hasattr(record, field)
        )
        return f"{rendered} {context}" if context else rendered


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if settings.log_json
        else PlainTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)
