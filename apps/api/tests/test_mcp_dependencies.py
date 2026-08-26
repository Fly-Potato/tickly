from collections.abc import Iterator
from datetime import timedelta
import hashlib
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.security import HTTPAuthorizationCredentials
import pytest
from sqlalchemy.orm import Session

from app.api.mcp_dependencies import get_mcp_current_user
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import issue_access_token
from app.db.session import create_engine_for_settings, create_session_factory
from app.models.user import utc_now
from app.services.accounts import create_account
from app.services.mcp_tokens import create_mcp_token


PASSWORD = "correct horse battery staple"


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database_url = f"sqlite:///{tmp_path / 'mcp-dependencies.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    settings = Settings(database_url=database_url, _env_file=None)
    engine = create_engine_for_settings(settings)
    factory = create_session_factory(engine)

    with factory() as database_session:
        yield database_session

    engine.dispose()


def bearer(raw_token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw_token)


def assert_authentication_required(
    raised: pytest.ExceptionInfo[AppError], *hidden_values: str
) -> None:
    error = raised.value
    assert error.status_code == 401
    assert error.code == "authentication_required"
    assert error.message == "需要 MCP 认证"
    assert error.headers == {"WWW-Authenticate": "Bearer"}
    rendered = str(error)
    for hidden in hidden_values:
        assert hidden not in rendered


def test_get_mcp_current_user_resolves_token_owner(session: Session) -> None:
    first = create_account(session, "first", PASSWORD)
    second = create_account(session, "second", PASSWORD)
    issued = create_mcp_token(session, second.id, "第二账号", None)

    resolved = get_mcp_current_user(session, bearer(issued.raw_token))

    assert resolved.id == second.id
    assert resolved.id != first.id


def test_get_mcp_current_user_maps_missing_credentials_to_bearer_challenge(
    session: Session,
) -> None:
    with pytest.raises(AppError) as raised:
        get_mcp_current_user(session, None)

    assert_authentication_required(raised)


@pytest.mark.parametrize("raw_token", ["wrong", "tickly_mcp_invalid.secret"])
def test_get_mcp_current_user_hides_wrong_and_malformed_tokens(
    session: Session,
    raw_token: str,
) -> None:
    with pytest.raises(AppError) as raised:
        get_mcp_current_user(session, bearer(raw_token))

    assert_authentication_required(
        raised,
        raw_token,
        hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
    )


@pytest.mark.parametrize("failure", ["expired", "revoked", "disabled"])
def test_get_mcp_current_user_hides_token_lifecycle_failures(
    session: Session,
    failure: str,
) -> None:
    user = create_account(session, failure, PASSWORD)
    issued = create_mcp_token(session, user.id, "生命周期", None)
    if failure == "expired":
        issued.record.expires_at = utc_now() - timedelta(seconds=1)
    elif failure == "revoked":
        issued.record.revoked_at = utc_now()
    else:
        user.is_active = False
    session.commit()

    with pytest.raises(AppError) as raised:
        get_mcp_current_user(session, bearer(issued.raw_token))

    assert_authentication_required(
        raised,
        issued.raw_token,
        issued.record.token_hash,
    )


def test_get_mcp_current_user_rejects_web_access_jwt(session: Session) -> None:
    settings = Settings(jwt_secret="s" * 64, _env_file=None)
    user = create_account(session, "web-user", PASSWORD)
    web_jwt = issue_access_token(user.id, user.auth_version, settings)

    with pytest.raises(AppError) as raised:
        get_mcp_current_user(session, bearer(web_jwt))

    assert_authentication_required(
        raised,
        web_jwt,
        hashlib.sha256(web_jwt.encode("utf-8")).hexdigest(),
    )


def test_get_mcp_current_user_does_not_hide_programming_errors(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_unexpectedly(*_: object) -> None:
        raise RuntimeError("programming error")

    monkeypatch.setattr(
        "app.api.mcp_dependencies.authenticate_mcp_token",
        fail_unexpectedly,
    )

    with pytest.raises(RuntimeError, match="programming error"):
        get_mcp_current_user(session, bearer("wrong"))
