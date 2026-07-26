import re

from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


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
