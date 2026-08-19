from collections.abc import Iterator
import hashlib
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.security import HTTPAuthorizationCredentials
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.mcp_dependencies import (
    McpAccountUnavailable,
    McpAuthenticationRequired,
    get_mcp_current_user,
    resolve_mcp_user,
    verify_mcp_token,
)
from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import User
from app.services.accounts import create_account


RAW_TOKEN = "test-mcp-token"
TOKEN_HASH = hashlib.sha256(RAW_TOKEN.encode("utf-8")).hexdigest()


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
        create_account(database_session, "potato", "correct horse battery staple")
        yield database_session

    engine.dispose()


def test_verify_mcp_token_accepts_only_matching_token() -> None:
    settings = Settings(mcp_token_sha256=TOKEN_HASH, _env_file=None)

    verify_mcp_token(RAW_TOKEN, settings)

    with pytest.raises(McpAuthenticationRequired):
        verify_mcp_token("wrong", settings)


def test_verify_mcp_token_uses_constant_time_digest_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compared: list[tuple[str, str]] = []

    def record_compare(actual: str, expected: str) -> bool:
        compared.append((actual, expected))
        return actual == expected

    monkeypatch.setattr("app.api.mcp_dependencies.secrets.compare_digest", record_compare)

    verify_mcp_token(
        RAW_TOKEN,
        Settings(mcp_token_sha256=TOKEN_HASH, _env_file=None),
    )

    assert compared == [(TOKEN_HASH, TOKEN_HASH)]


def test_verify_mcp_token_fails_closed_without_configuration() -> None:
    with pytest.raises(McpAuthenticationRequired):
        verify_mcp_token(RAW_TOKEN, Settings(_env_file=None))


def test_resolve_mcp_user_returns_the_only_active_account(session: Session) -> None:
    user = session.scalar(select(User))

    assert user is not None
    assert resolve_mcp_user(session).id == user.id


def test_resolve_mcp_user_rejects_an_inactive_account(session: Session) -> None:
    user = session.scalar(select(User))
    assert user is not None
    user.is_active = False
    session.commit()

    with pytest.raises(McpAccountUnavailable):
        resolve_mcp_user(session)


def test_resolve_mcp_user_rejects_an_empty_database(session: Session) -> None:
    user = session.scalar(select(User))
    assert user is not None
    session.delete(user)
    session.commit()

    with pytest.raises(McpAccountUnavailable):
        resolve_mcp_user(session)


def test_resolve_mcp_user_rejects_multiple_accounts(session: Session) -> None:
    session.add(User(username="second", password_hash="not-a-real-password-hash"))
    session.commit()

    with pytest.raises(McpAccountUnavailable):
        resolve_mcp_user(session)


def test_mcp_dependency_maps_missing_credentials_to_bearer_challenge(
    session: Session,
) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(mcp_token_sha256=TOKEN_HASH, _env_file=None)
            )
        )
    )

    with pytest.raises(AppError) as raised:
        get_mcp_current_user(request, session, None)  # type: ignore[arg-type]

    assert raised.value.status_code == 401
    assert raised.value.code == "authentication_required"
    assert raised.value.headers == {"WWW-Authenticate": "Bearer"}


def test_mcp_dependency_maps_account_failure_without_exposing_details(
    session: Session,
) -> None:
    user = session.scalar(select(User))
    assert user is not None
    user.is_active = False
    session.commit()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(mcp_token_sha256=TOKEN_HASH, _env_file=None)
            )
        )
    )
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=RAW_TOKEN,
    )

    with pytest.raises(AppError) as raised:
        get_mcp_current_user(  # type: ignore[arg-type]
            request,
            session,
            credentials,
        )

    assert raised.value.status_code == 503
    assert raised.value.code == "mcp_account_unavailable"
    assert raised.value.message == "MCP 账号不可用"
