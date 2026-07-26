import json
import logging

from app.core.logging import JsonFormatter


def test_json_formatter_emits_access_fields() -> None:
    record = logging.LogRecord(name="tickly.access", level=logging.INFO, pathname=__file__, lineno=10, msg="request.completed", args=(), exc_info=None)
    record.request_id = "json-log"
    record.method = "GET"
    record.path = "/health"
    record.status = 200
    record.duration_ms = 1.25
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "request.completed"
    assert payload["request_id"] == "json-log"
    assert payload["method"] == "GET"
    assert payload["path"] == "/health"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.25
    assert payload["timestamp"].endswith("+00:00")
