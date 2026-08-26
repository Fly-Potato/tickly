from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
import hashlib
import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.cli import main as cli_main
from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings
from app.main import create_app
from app.models import McpToken


PASSWORD = "correct horse battery staple"
TOKEN_ITEM_FIELDS = {
    "id",
    "name",
    "status",
    "expires_at",
    "last_used_at",
    "created_at",
}


@pytest.fixture
def token_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """通过维护 CLI 创建两个账号，再挂载同一数据库的 Web API。"""

    database_path = tmp_path / "mcp-tokens-api.db"
    database_url = f"sqlite:///{database_path}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    monkeypatch.setenv("TICKLY_DATABASE_URL", database_url)
    monkeypatch.setenv("TICKLY_ENVIRONMENT", "test")
    answers = iter([PASSWORD, PASSWORD, PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda _: next(answers))
    assert cli_main(["user", "create", "--username", "first"]) == 0
    assert cli_main(["user", "create", "--username", "second"]) == 0

    settings = Settings(
        environment=Environment.TEST,
        database_url=database_url,
        jwt_secret="s" * 64,
        _env_file=None,
    )
    engine = create_engine_for_settings(settings)
    application = create_app(settings, database_engine=engine)
    with TestClient(application) as client:
        yield client
    engine.dispose()


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)
    return parsed


def test_user_can_create_list_and_revoke_only_owned_tokens(
    token_client: TestClient,
) -> None:
    first_access = login(token_client, "first")
    created = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(first_access),
        json={"name": "  家中 Codex  ", "expires_in_days": 365},
    )

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert set(body) == {"item", "token"}
    assert set(body["item"]) == TOKEN_ITEM_FIELDS
    assert body["item"]["name"] == "家中 Codex"
    assert body["item"]["status"] == "active"
    assert body["item"]["last_used_at"] is None
    token_id = body["item"]["id"]
    raw_token = body["token"]
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    assert raw_token.startswith(f"tickly_mcp_{token_id}.")
    parse_utc(body["item"]["created_at"])
    parse_utc(body["item"]["expires_at"])
    assert digest not in created.text
    assert first_access not in created.text

    listed = token_client.get(
        "/api/v1/mcp-tokens", headers=bearer(first_access)
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == [body["item"]]
    assert raw_token not in listed.text
    assert digest not in listed.text
    assert "token_hash" not in listed.text
    assert "user_id" not in listed.text
    assert first_access not in listed.text

    second_access = login(token_client, "second")
    hidden_request_headers = {
        **bearer(second_access),
        "X-Request-ID": "hidden-mcp-token",
    }
    foreign = token_client.delete(
        f"/api/v1/mcp-tokens/{token_id}", headers=hidden_request_headers
    )
    missing = token_client.delete(
        "/api/v1/mcp-tokens/00000000-0000-0000-0000-999999999999",
        headers=hidden_request_headers,
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == "mcp_token_not_found"
    assert missing.json()["error"]["code"] == "mcp_token_not_found"
    assert foreign.json()["error"]["message"] == "MCP Token 不存在"
    assert foreign.json() == missing.json()
    for secret in (raw_token, digest, first_access, second_access):
        assert secret not in foreign.text
        assert secret not in missing.text

    revoked = token_client.delete(
        f"/api/v1/mcp-tokens/{token_id}", headers=bearer(first_access)
    )
    repeated = token_client.delete(
        f"/api/v1/mcp-tokens/{token_id}", headers=bearer(first_access)
    )
    assert revoked.status_code == repeated.status_code == 204
    assert revoked.content == repeated.content == b""

    after_revoke = token_client.get(
        "/api/v1/mcp-tokens", headers=bearer(first_access)
    )
    assert after_revoke.json()["items"][0]["status"] == "revoked"


def test_list_only_returns_current_users_tokens(token_client: TestClient) -> None:
    first_access = login(token_client, "first")
    second_access = login(token_client, "second")
    first = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(first_access),
        json={"name": "第一账号专用", "expires_in_days": 365},
    ).json()
    second = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(second_access),
        json={"name": "第二账号专用", "expires_in_days": None},
    ).json()
    first_digest = hashlib.sha256(first["token"].encode("utf-8")).hexdigest()
    second_digest = hashlib.sha256(second["token"].encode("utf-8")).hexdigest()

    first_list = token_client.get(
        "/api/v1/mcp-tokens", headers=bearer(first_access)
    )
    second_list = token_client.get(
        "/api/v1/mcp-tokens", headers=bearer(second_access)
    )

    assert first_list.status_code == second_list.status_code == 200
    assert [item["id"] for item in first_list.json()["items"]] == [
        first["item"]["id"]
    ]
    assert [item["id"] for item in second_list.json()["items"]] == [
        second["item"]["id"]
    ]
    for hidden in (
        second["item"]["id"],
        second["item"]["name"],
        second["token"],
        second_digest,
    ):
        assert hidden not in first_list.text
    for hidden in (
        first["item"]["id"],
        first["item"]["name"],
        first["token"],
        first_digest,
    ):
        assert hidden not in second_list.text


@pytest.mark.parametrize(
    ("payload", "expected_days"),
    [
        ({"name": "默认有效期"}, 365),
        ({"name": "九十天", "expires_in_days": 90}, 90),
        ({"name": "三百六十五天", "expires_in_days": 365}, 365),
        ({"name": "永不过期", "expires_in_days": None}, None),
    ],
)
def test_create_accepts_only_supported_expiry_choices(
    token_client: TestClient,
    payload: dict[str, object],
    expected_days: int | None,
) -> None:
    access = login(token_client, "first")
    before = datetime.now(UTC)
    response = token_client.post(
        "/api/v1/mcp-tokens", headers=bearer(access), json=payload
    )
    after = datetime.now(UTC)

    assert response.status_code == 201
    created_at = parse_utc(response.json()["item"]["created_at"])
    expires_at_value = response.json()["item"]["expires_at"]
    if expected_days is None:
        assert expires_at_value is None
    else:
        expires_at = parse_utc(expires_at_value)
        assert before + timedelta(days=expected_days) <= expires_at
        assert expires_at <= after + timedelta(days=expected_days)
        assert abs((created_at + timedelta(days=expected_days)) - expires_at) < timedelta(
            seconds=1
        )


@pytest.mark.parametrize(
    "expires_in_days",
    [0, 30, 366, -90, "90", "365", True, False, 90.0],
)
def test_create_rejects_invalid_expiry_types_and_values(
    token_client: TestClient,
    expires_in_days: object,
) -> None:
    access = login(token_client, "first")
    response = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(access),
        json={"name": "无效有效期", "expires_in_days": expires_in_days},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "name",
    ["", "   ", "\0", "有效\0名称", "x" * 65, 123, True, None],
)
def test_create_rejects_invalid_name_boundaries(
    token_client: TestClient,
    name: object,
) -> None:
    access = login(token_client, "first")
    response = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(access),
        json={"name": name, "expires_in_days": 365},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("normalized_name", ["一", "界" * 64])
def test_create_accepts_trimmed_name_length_boundaries(
    token_client: TestClient,
    normalized_name: str,
) -> None:
    access = login(token_client, "first")
    response = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(access),
        json={"name": f"  {normalized_name}  ", "expires_in_days": 365},
    )

    assert response.status_code == 201
    assert response.json()["item"]["name"] == normalized_name


def test_create_rejects_client_supplied_owner_and_unknown_fields(
    token_client: TestClient,
) -> None:
    access = login(token_client, "first")

    for payload in (
        {"name": "越权", "user_id": "secret-owner"},
        {"name": "额外字段", "unexpected": "secret-extra"},
    ):
        response = token_client.post(
            "/api/v1/mcp-tokens", headers=bearer(access), json=payload
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert "secret-owner" not in response.text
        assert "secret-extra" not in response.text


def test_list_is_newest_first_and_revoked_status_precedes_expired(
    token_client: TestClient,
) -> None:
    access = login(token_client, "first")
    first = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(access),
        json={"name": "较早", "expires_in_days": None},
    ).json()["item"]
    second = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(access),
        json={"name": "已过期", "expires_in_days": 90},
    ).json()["item"]
    third = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(access),
        json={"name": "过期且已撤销", "expires_in_days": 90},
    ).json()["item"]

    now = datetime.now(UTC)
    with token_client.app.state.database_session_factory() as session:
        first_record = session.get(McpToken, first["id"])
        second_record = session.get(McpToken, second["id"])
        third_record = session.get(McpToken, third["id"])
        assert first_record is not None
        assert second_record is not None
        assert third_record is not None
        first_record.created_at = now - timedelta(days=3)
        first_record.last_used_at = datetime(2026, 8, 25, 3, 4, 5)
        second_record.created_at = now - timedelta(days=2)
        second_record.expires_at = now - timedelta(days=3)
        third_record.created_at = now - timedelta(days=1)
        third_record.expires_at = now - timedelta(days=4)
        third_record.revoked_at = now - timedelta(days=2)
        session.commit()

    response = token_client.get(
        "/api/v1/mcp-tokens", headers=bearer(access)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [
        third["id"],
        second["id"],
        first["id"],
    ]
    assert items[0]["status"] == "revoked"
    assert items[1]["status"] == "expired"
    assert items[2]["status"] == "active"
    assert parse_utc(items[2]["last_used_at"]) == datetime(
        2026, 8, 25, 3, 4, 5, tzinfo=UTC
    )
    for item in items:
        parse_utc(item["created_at"])
        if item["expires_at"] is not None:
            parse_utc(item["expires_at"])


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/mcp-tokens", None),
        ("post", "/api/v1/mcp-tokens", {"name": "未登录"}),
        (
            "delete",
            "/api/v1/mcp-tokens/00000000-0000-0000-0000-999999999999",
            None,
        ),
    ],
)
def test_all_token_operations_require_web_authentication(
    token_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = token_client.request(method, path, json=payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_token_creation_and_validation_errors_do_not_log_credentials(
    token_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    access = login(token_client, "first")
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        created = token_client.post(
            "/api/v1/mcp-tokens",
            headers=bearer(access),
            json={"name": "日志安全", "expires_in_days": 365},
        )
        assert created.status_code == 201
        raw_token = created.json()["token"]
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        listed = token_client.get(
            "/api/v1/mcp-tokens", headers=bearer(access)
        )
        invalid = token_client.post(
            "/api/v1/mcp-tokens",
            headers=bearer(access),
            json={
                "name": raw_token,
                "user_id": digest,
                "expires_in_days": 365,
            },
        )

    assert listed.status_code == 200
    assert invalid.status_code == 422
    for secret in (raw_token, digest, access):
        assert secret not in listed.text
        assert secret not in invalid.text
        assert secret not in caplog.text
