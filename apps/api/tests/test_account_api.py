from collections.abc import Iterator
import hashlib
import logging
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.services.accounts as account_services
from app.core.config import Environment, Settings
from app.core import logging as logging_config
from app.core.security import verify_password
from app.db.session import create_engine_for_settings, create_session_factory
from app.main import create_app
from app.models import AuthSession, McpToken, User
from app.services.accounts import create_account


PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct password"
MCP_TOKEN = "tickly_mcp_commit_failure_secret"
NEW_HASH_SENTINEL = "new-password-hash-sentinel"
SQL_SENTINEL = "UPDATE users SET password_hash=:password_hash /* secret sql */"
PARAMS_SENTINEL = "secret-sql-params-sentinel"
OVERSIZED_PASSWORD = "oversized-password-secret-" + "x" * 1024


@pytest.fixture
def account_client(tmp_path: Path) -> Iterator[TestClient]:
    """创建完成 migration 的账号 API 实例，避免测试依赖开发数据库。"""

    database_path = tmp_path / "account-api.db"
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


def login(
    account_client: TestClient,
    password: str = PASSWORD,
) -> tuple[str, str]:
    response = account_client.post(
        "/api/v1/auth/login",
        json={"username": "potato", "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"], account_client.cookies["tickly_refresh"]


def authorization(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_change_password_revokes_all_browser_sessions_and_old_access(
    account_client: TestClient,
) -> None:
    first_access, first_refresh = login(account_client)
    second_access, second_refresh = login(account_client)
    with account_client.app.state.database_session_factory() as session:
        user = session.scalar(select(User).where(User.username == "potato"))
        assert user is not None
        original_session_ids = set(
            session.scalars(
                select(AuthSession.id).where(AuthSession.user_id == user.id)
            )
        )

    response = account_client.put(
        "/api/v1/account/password",
        headers=authorization(second_access),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "Path=/api/v1/auth" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]

    for access_token in (first_access, second_access):
        stale_access = account_client.get(
            "/api/v1/auth/me",
            headers=authorization(access_token),
        )
        assert stale_access.status_code == 401
        assert stale_access.json()["error"]["code"] == "authentication_required"

    for refresh_token in (first_refresh, second_refresh):
        with TestClient(account_client.app) as stale_client:
            stale_refresh = stale_client.post(
                "/api/v1/auth/refresh",
                headers={"Cookie": f"tickly_refresh={refresh_token}"},
            )
        assert stale_refresh.status_code == 401

    new_access, _ = login(account_client, NEW_PASSWORD)
    current = account_client.get(
        "/api/v1/auth/me",
        headers=authorization(new_access),
    )
    assert current.status_code == 200

    with account_client.app.state.database_session_factory() as session:
        user = session.scalar(select(User).where(User.username == "potato"))
        assert user is not None
        sessions = list(
            session.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == user.id)
                .order_by(AuthSession.created_at)
            )
        )
        # 密码变更前的两条会话均撤销，之后的新密码登录产生一条活跃会话。
        assert len(sessions) == 3
        assert len(original_session_ids) == 2
        assert all(
            auth_session.revoked_at is not None
            for auth_session in sessions
            if auth_session.id in original_session_ids
        )
        assert sum(auth_session.revoked_at is None for auth_session in sessions) == 1


@pytest.mark.parametrize(
    ("current_password", "new_password", "expected_code"),
    [
        ("wrong current secret", NEW_PASSWORD, "invalid_current_password"),
        (PASSWORD, PASSWORD, "password_unchanged"),
    ],
)
def test_change_password_domain_failures_are_safe_and_do_not_mutate_state(
    account_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    current_password: str,
    new_password: str,
    expected_code: str,
) -> None:
    access_token, _ = login(account_client)
    caplog.clear()

    with account_client.app.state.database_session_factory() as session:
        user = session.scalar(select(User).where(User.username == "potato"))
        assert user is not None
        original_hash = user.password_hash
        original_version = user.auth_version
        original_sessions = [
            (auth_session.id, auth_session.revoked_at)
            for auth_session in session.scalars(
                select(AuthSession).where(AuthSession.user_id == user.id)
            )
        ]

    with caplog.at_level(logging.DEBUG):
        response = account_client.put(
            "/api/v1/account/password",
            headers=authorization(access_token),
            json={
                "current_password": current_password,
                "new_password": new_password,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code
    for secret in (current_password, new_password, access_token, original_hash):
        assert secret not in response.text
        assert secret not in caplog.text

    with account_client.app.state.database_session_factory() as session:
        user = session.scalar(select(User).where(User.username == "potato"))
        assert user is not None
        persisted_sessions = [
            (auth_session.id, auth_session.revoked_at)
            for auth_session in session.scalars(
                select(AuthSession).where(AuthSession.user_id == user.id)
            )
        ]
        assert user.password_hash == original_hash
        assert user.auth_version == original_version
        assert persisted_sessions == original_sessions
        assert verify_password(PASSWORD, user.password_hash)


def test_change_password_commit_failure_is_sanitized_and_rolls_back(
    account_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token, refresh_token = login(account_client)
    mcp_token_hash = hashlib.sha256(MCP_TOKEN.encode("utf-8")).hexdigest()
    with account_client.app.state.database_session_factory() as session:
        user = session.scalar(select(User).where(User.username == "potato"))
        assert user is not None
        user_id = user.id
        old_hash = user.password_hash
        old_version = user.auth_version
        mcp_token = McpToken(
            user_id=user_id,
            name="提交失败边界",
            token_hash=mcp_token_hash,
        )
        session.add(mcp_token)
        session.commit()
        mcp_token_id = mcp_token.id
        original_sessions = [
            (auth_session.id, auth_session.revoked_at)
            for auth_session in session.scalars(
                select(AuthSession).where(AuthSession.user_id == user_id)
            )
        ]

    failed_sessions: list[Session] = []
    generated_hashes: list[str] = []

    def fail_password_change_commit(database_session: Session) -> None:
        pending_user = database_session.get(User, user_id)
        assert pending_user is not None
        assert pending_user.auth_version == old_version + 1
        assert verify_password(NEW_PASSWORD, pending_user.password_hash)
        pending_revocations = list(
            database_session.scalars(
                select(AuthSession.revoked_at).where(
                    AuthSession.user_id == user_id
                )
            )
        )
        assert pending_revocations
        assert all(revoked_at is not None for revoked_at in pending_revocations)
        failed_sessions.append(database_session)
        generated_hashes.append(pending_user.password_hash)
        raise IntegrityError(
            SQL_SENTINEL,
            {
                "password_hash": NEW_HASH_SENTINEL,
                "old_hash": old_hash,
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "mcp_token": MCP_TOKEN,
                "params": PARAMS_SENTINEL,
            },
            RuntimeError(PARAMS_SENTINEL),
        )

    caplog.clear()
    account_logger = logging.getLogger("tickly.account")
    account_logger_was_disabled = account_logger.disabled
    account_logger.disabled = False
    with monkeypatch.context() as commit_failure:
        commit_failure.setattr(Session, "commit", fail_password_change_commit)
        try:
            with caplog.at_level(logging.ERROR, logger="tickly.account"):
                response = account_client.put(
                    "/api/v1/account/password",
                    headers={
                        **authorization(access_token),
                        "X-Request-ID": "password-change-database-failed",
                    },
                    json={
                        "current_password": PASSWORD,
                        "new_password": NEW_PASSWORD,
                    },
                )
        finally:
            account_logger.disabled = account_logger_was_disabled

    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == "password-change-database-failed"
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "服务器内部错误"
    assert "set-cookie" not in response.headers
    assert len(failed_sessions) == 1
    assert len(generated_hashes) == 1
    safe_events = [
        record
        for record in caplog.records
        if record.getMessage() == "account.password_change.failed"
    ]
    assert len(safe_events) == 1
    safe_event = safe_events[0]
    assert safe_event.name == "tickly.account"
    assert safe_event.request_id != "password-change-database-failed"
    UUID(safe_event.request_id)
    assert safe_event.user_id == user_id
    assert safe_event.stage == "database"
    assert safe_event.exc_info is None
    assert safe_event.exc_text is None

    for secret in (
        PASSWORD,
        NEW_PASSWORD,
        old_hash,
        generated_hashes[0],
        NEW_HASH_SENTINEL,
        access_token,
        refresh_token,
        MCP_TOKEN,
        mcp_token_hash,
        SQL_SENTINEL,
        PARAMS_SENTINEL,
    ):
        assert secret not in response.text
        assert secret not in caplog.text

    # 请求依赖关闭后，同一个 Session 仍可复用，证明 rollback 已恢复事务边界。
    failed_session = failed_sessions[0]
    persisted_user = failed_session.get(User, user_id)
    persisted_mcp_token = failed_session.get(McpToken, mcp_token_id)
    persisted_sessions = [
        (auth_session.id, auth_session.revoked_at)
        for auth_session in failed_session.scalars(
            select(AuthSession).where(AuthSession.user_id == user_id)
        )
    ]
    assert persisted_user is not None
    assert persisted_user.password_hash == old_hash
    assert persisted_user.auth_version == old_version
    assert persisted_sessions == original_sessions
    assert persisted_mcp_token is not None
    assert persisted_mcp_token.token_hash == mcp_token_hash
    assert persisted_mcp_token.revoked_at is None
    failed_session.close()

    # 应用也必须继续可用，且只有旧密码仍能登录。
    old_password_login = account_client.post(
        "/api/v1/auth/login",
        json={"username": "potato", "password": PASSWORD},
    )
    new_password_login = account_client.post(
        "/api/v1/auth/login",
        json={"username": "potato", "password": NEW_PASSWORD},
    )
    assert old_password_login.status_code == 200
    assert new_password_login.status_code == 401


def test_change_password_cleanup_database_failures_preserve_safe_response(
    account_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token, refresh_token = login(account_client)
    failed_sessions: list[Session] = []
    generated_hashes: list[str] = []
    rollback_calls: list[Session] = []
    invalidated_sessions: list[Session] = []
    real_invalidate = Session.invalidate

    def fail_commit(database_session: Session) -> None:
        pending_user = database_session.scalar(
            select(User).where(User.username == "potato")
        )
        assert pending_user is not None
        assert verify_password(NEW_PASSWORD, pending_user.password_hash)
        failed_sessions.append(database_session)
        generated_hashes.append(pending_user.password_hash)
        raise IntegrityError(
            "UPDATE users SET password_hash=:cleanup_secret",
            {
                "cleanup_secret": "commit-cleanup-secret",
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
            RuntimeError("commit-cleanup-secret"),
        )

    def fail_rollback(database_session: Session) -> None:
        rollback_calls.append(database_session)
        raise IntegrityError(
            "ROLLBACK /* rollback-cleanup-secret */",
            {"secret": "rollback-cleanup-secret"},
            RuntimeError("rollback-cleanup-secret"),
        )

    def observe_invalidate(database_session: Session) -> None:
        invalidated_sessions.append(database_session)
        real_invalidate(database_session)

    caplog.clear()
    account_logger = logging.getLogger("tickly.account")
    account_logger_was_disabled = account_logger.disabled
    account_logger.disabled = False
    with monkeypatch.context() as cleanup_failure:
        cleanup_failure.setattr(Session, "commit", fail_commit)
        cleanup_failure.setattr(Session, "rollback", fail_rollback)
        cleanup_failure.setattr(Session, "invalidate", observe_invalidate)
        try:
            with caplog.at_level(logging.ERROR, logger="tickly.account"):
                response = account_client.put(
                    "/api/v1/account/password",
                    headers={
                        **authorization(access_token),
                        "X-Request-ID": "cleanup-database-failed",
                    },
                    json={
                        "current_password": PASSWORD,
                        "new_password": NEW_PASSWORD,
                    },
                )
        finally:
            account_logger.disabled = account_logger_was_disabled

    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == "cleanup-database-failed"
    assert response.json()["error"]["code"] == "internal_error"
    assert "set-cookie" not in response.headers
    assert len(failed_sessions) == 1
    assert len(rollback_calls) >= 2
    assert failed_sessions[0] in invalidated_sessions

    safe_events = [
        record
        for record in caplog.records
        if record.getMessage() == "account.password_change.failed"
    ]
    assert len(safe_events) == 1
    safe_event = safe_events[0]
    assert safe_event.request_id != "cleanup-database-failed"
    UUID(safe_event.request_id)
    assert safe_event.stage == "database"
    assert safe_event.exc_info is None

    formatted_logs = (
        logging_config.JsonFormatter().format(safe_event)
        + logging_config.PlainTextFormatter(
            "%(levelname)s %(name)s %(message)s"
        ).format(safe_event)
    )
    for secret in (
        PASSWORD,
        NEW_PASSWORD,
        generated_hashes[0],
        access_token,
        refresh_token,
        "commit-cleanup-secret",
        "rollback-cleanup-secret",
        "UPDATE users",
        "ROLLBACK",
    ):
        assert secret not in response.text
        assert secret not in caplog.text
        assert secret not in formatted_logs

    # cleanup 失败的 Session 已废弃；恢复原方法后，新请求必须取得新 Session 并成功。
    subsequent = account_client.post(
        "/api/v1/auth/login",
        json={"username": "potato", "password": PASSWORD},
    )
    assert subsequent.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"current_password": PASSWORD, "new_password": "short"},
        {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "user_id": "someone-else",
        },
    ],
)
def test_change_password_rejects_invalid_or_identity_fields(
    account_client: TestClient,
    payload: dict[str, str],
) -> None:
    access_token, _ = login(account_client)

    response = account_client.put(
        "/api/v1/account/password",
        headers=authorization(access_token),
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert payload["current_password"] not in response.text
    assert payload["new_password"] not in response.text
    assert access_token not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"current_password": OVERSIZED_PASSWORD, "new_password": NEW_PASSWORD},
        {"current_password": PASSWORD, "new_password": OVERSIZED_PASSWORD},
    ],
)
def test_change_password_rejects_oversized_input_before_argon2(
    account_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
) -> None:
    access_token, _ = login(account_client)
    argon2_calls: list[str] = []
    real_verify_password = account_services.verify_password
    real_hash_password = account_services.hash_password

    def observe_verify_password(value: str, encoded: str) -> bool:
        argon2_calls.append("verify")
        return real_verify_password(value, encoded)

    def observe_hash_password(value: str) -> str:
        argon2_calls.append("hash")
        return real_hash_password(value)

    monkeypatch.setattr(
        account_services,
        "verify_password",
        observe_verify_password,
    )
    monkeypatch.setattr(account_services, "hash_password", observe_hash_password)
    caplog.clear()
    root_logger = logging.getLogger()
    root_logger.addHandler(caplog.handler)

    try:
        with caplog.at_level(logging.DEBUG):
            response = account_client.put(
                "/api/v1/account/password",
                headers=authorization(access_token),
                json=payload,
            )
    finally:
        root_logger.removeHandler(caplog.handler)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert argon2_calls == []
    assert OVERSIZED_PASSWORD not in response.text
    assert OVERSIZED_PASSWORD not in caplog.text


def test_change_password_requires_access_token(account_client: TestClient) -> None:
    response = account_client.put(
        "/api/v1/account/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_openapi_contains_account_password_operation(
    account_client: TestClient,
) -> None:
    paths = account_client.get("/openapi.json").json()["paths"]

    assert "/api/v1/account/password" in paths
