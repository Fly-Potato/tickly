import logging
import re
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app.core.config import Environment, Settings
from app.main import create_app


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
PAT_LIKE_REQUEST_ID = (
    "tickly_mcp_12345678-1234-1234-1234-123456789abc."
    "abcdefghijklmnopqrstuvwxyzABCDEFGH123456789"
)


def make_client() -> TestClient:
    app = create_app(Settings(environment=Environment.TEST, _env_file=None))
    return TestClient(app)


def test_missing_request_id_is_generated() -> None:
    with make_client() as client:
        response = client.get("/health")
    request_id = response.headers["X-Request-ID"]
    assert REQUEST_ID_PATTERN.fullmatch(request_id)


def test_valid_request_id_is_preserved() -> None:
    with make_client() as client:
        response = client.get("/health", headers={"X-Request-ID": "web.request-123"})
    assert response.headers["X-Request-ID"] == "web.request-123"


def test_invalid_request_id_is_replaced() -> None:
    with make_client() as client:
        response = client.get("/health", headers={"X-Request-ID": "contains a space"})
    assert response.headers["X-Request-ID"] != "contains a space"
    assert REQUEST_ID_PATTERN.fullmatch(response.headers["X-Request-ID"])


def test_overlong_request_id_is_replaced() -> None:
    supplied = "x" * 129
    with make_client() as client:
        response = client.get("/health", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] != supplied
    assert len(response.headers["X-Request-ID"]) <= 128


@pytest.mark.parametrize(
    "supplied",
    [PAT_LIKE_REQUEST_ID, "a" * 64, "business.request-123"],
    ids=["pat", "digest", "business-id"],
)
def test_protocol_request_id_is_preserved_but_access_log_uses_server_id(
    supplied: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """客户端关联 ID 可回传，但访问日志只能使用独立服务端 UUID。"""
    caplog.clear()
    client = make_client()
    root_logger = logging.getLogger()
    root_logger.addHandler(caplog.handler)
    try:
        with client:
            with caplog.at_level(logging.INFO, logger="tickly.access"):
                response = client.get("/health", headers={"X-Request-ID": supplied})
    finally:
        root_logger.removeHandler(caplog.handler)

    records = [
        record
        for record in caplog.records
        if record.name == "tickly.access"
        and record.getMessage() == "request.completed"
    ]
    assert response.headers["X-Request-ID"] == supplied
    assert len(records) == 1
    assert records[0].request_id != supplied
    UUID(records[0].request_id)
    assert supplied not in caplog.text
