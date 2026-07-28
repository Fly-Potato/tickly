from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings, create_session_factory
from app.main import create_app
from app.services.accounts import create_account, deactivate_account


PASSWORD = "correct horse battery staple"


@pytest.fixture
def auth_client(tmp_path: Path) -> Iterator[TestClient]:
    database_path = tmp_path / "auth-api.db"
    database_url = f"sqlite:///{database_path}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    settings = Settings(
        environment=Environment.TEST,
        database_url=database_url,
        jwt_secret="s" * 64,
        _env_file=None,
    )
    engine = create_engine_for_settings(settings)
    with create_session_factory(engine)() as session:
        create_account(session, "potato", PASSWORD)
    application = create_app(settings, database_engine=engine)

    with TestClient(application) as client:
        yield client

    engine.dispose()


def login(auth_client: TestClient):
    return auth_client.post(
        "/api/v1/auth/login",
        json={"username": "Potato", "password": PASSWORD},
    )


def test_login_sets_refresh_cookie_and_returns_access_token(
    auth_client: TestClient,
) -> None:
    response = login(auth_client)

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 900
    assert "refresh_token" not in response.json()
    cookie = response.headers["set-cookie"]
    assert "tickly_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie


@pytest.mark.parametrize(
    ("username", "password"),
    [("missing", PASSWORD), ("potato", "wrong password")],
)
def test_login_failures_use_one_safe_response(
    auth_client: TestClient, username: str, password: str
) -> None:
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Request-ID": "login-failed"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "invalid_credentials",
            "message": "用户名或密码错误",
            "request_id": "login-failed",
            "details": [],
        }
    }
    assert password not in response.text


def test_me_requires_bearer_and_returns_only_public_user_fields(
    auth_client: TestClient,
) -> None:
    missing = auth_client.get("/api/v1/auth/me")
    invalid = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-token"}
    )
    token = login(auth_client).json()["access_token"]
    response = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    assert invalid.json()["error"]["code"] == "authentication_required"
    assert response.status_code == 200
    assert response.json() == {
        "id": response.json()["id"],
        "username": "potato",
        "timezone": "Asia/Shanghai",
        "is_active": True,
    }
    assert "password_hash" not in response.text


def test_refresh_rotates_cookie_and_replay_revokes_the_session(
    auth_client: TestClient,
) -> None:
    first_login = login(auth_client)
    old_refresh = auth_client.cookies["tickly_refresh"]
    refreshed = auth_client.post("/api/v1/auth/refresh")
    new_refresh = auth_client.cookies["tickly_refresh"]

    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != first_login.json()["access_token"]
    assert new_refresh != old_refresh

    with TestClient(auth_client.app) as replay_client:
        replay = replay_client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": f"tickly_refresh={old_refresh}"},
        )

    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_replayed"
    assert "Max-Age=0" in replay.headers["set-cookie"]
    assert "Path=/api/v1/auth" in replay.headers["set-cookie"]


def test_refresh_requires_cookie_and_clears_invalid_cookie(
    auth_client: TestClient,
) -> None:
    response = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Cookie": "tickly_refresh=not-a-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_required"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "not-a-token" not in response.text


def test_logout_is_idempotent_and_always_clears_cookie(
    auth_client: TestClient,
) -> None:
    login(auth_client)

    first = auth_client.post("/api/v1/auth/logout")
    second = auth_client.post("/api/v1/auth/logout")

    assert first.status_code == 204
    assert first.content == b""
    assert second.status_code == 204
    assert "Max-Age=0" in first.headers["set-cookie"]
    assert "SameSite=strict" in first.headers["set-cookie"]


def test_deactivated_user_cannot_use_existing_access_token(
    auth_client: TestClient,
) -> None:
    token = login(auth_client).json()["access_token"]
    with auth_client.app.state.database_session_factory() as session:
        deactivate_account(session, "potato")

    response = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_openapi_contains_the_four_authentication_operations(
    auth_client: TestClient,
) -> None:
    paths = auth_client.get("/openapi.json").json()["paths"]

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/refresh" in paths
    assert "/api/v1/auth/logout" in paths
    assert "/api/v1/auth/me" in paths
