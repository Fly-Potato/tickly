import sqlite3

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Environment, Settings
from app.main import create_app


def make_app():
    app = create_app(Settings(environment=Environment.TEST, _env_file=None))

    @app.get("/test/validate/{item_id}")
    async def validate_item_id(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/test/http-error")
    async def raise_http_error() -> None:
        raise HTTPException(status_code=418, detail="Short and stout")

    @app.get("/test/boom")
    async def raise_unhandled_error() -> None:
        raise RuntimeError("secret internal text")

    @app.get("/test/database-busy")
    async def raise_database_busy() -> None:
        raise OperationalError(
            "SELECT secret_value",
            {},
            sqlite3.OperationalError("database is locked"),
        )

    @app.get("/test/database-error")
    async def raise_other_database_error() -> None:
        raise OperationalError(
            "SELECT secret_value",
            {},
            sqlite3.OperationalError("no such table: secret_table"),
        )

    return app


def test_unknown_route_uses_uniform_error() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/missing", headers={"X-Request-ID": "missing-route"})
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "资源不存在", "request_id": "missing-route", "details": []}}


def test_validation_error_uses_uniform_error() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/test/validate/not-an-integer", headers={"X-Request-ID": "validation"})
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "请求参数无效"
    assert body["error"]["request_id"] == "validation"
    assert body["error"]["details"][0]["location"] == ["path", "item_id"]
    assert "input" not in body["error"]["details"][0]


def test_explicit_http_error_preserves_status() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/test/http-error")
    assert response.status_code == 418
    assert response.json()["error"]["code"] == "http_error"
    assert response.json()["error"]["message"] == "Short and stout"


def test_unhandled_error_does_not_leak_exception_text() -> None:
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        response = client.get("/test/boom", headers={"X-Request-ID": "boom"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "boom"
    assert response.json() == {"error": {"code": "internal_error", "message": "服务器内部错误", "request_id": "boom", "details": []}}
    assert "secret internal text" not in response.text


def test_sqlite_busy_error_is_a_stable_retryable_response() -> None:
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        response = client.get(
            "/test/database-busy", headers={"X-Request-ID": "busy-request"}
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_busy",
            "message": "数据库繁忙，请稍后重试",
            "request_id": "busy-request",
            "details": [],
        }
    }
    assert "SELECT secret_value" not in response.text
    assert "database is locked" not in response.text


def test_other_operational_error_remains_a_sanitized_internal_error() -> None:
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        response = client.get(
            "/test/database-error", headers={"X-Request-ID": "database-error"}
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "SELECT secret_value" not in response.text
    assert "secret_table" not in response.text
