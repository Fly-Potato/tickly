from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Environment, Settings
from app.core.security import digest_refresh_token
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import AuthSession
from app.services.accounts import create_account, deactivate_account
from app.services.auth import (
    AuthenticationRequired,
    InvalidCredentials,
    RefreshReplayed,
    RefreshRequired,
    authenticate_access_token,
    login_user,
    logout_session,
    refresh_session,
)


PASSWORD = "correct horse battery staple"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        jwt_secret="s" * 64,
        _env_file=None,
    )


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database_path = tmp_path / "auth-service.db"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(alembic_config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{database_path}"})()
    )
    session_factory = create_session_factory(engine)

    with session_factory() as database_session:
        yield database_session

    engine.dispose()


def test_login_creates_a_fixed_expiry_session_and_truncates_user_agent(
    session: Session, settings: Settings
) -> None:
    user = create_account(session, "potato", PASSWORD)
    before = datetime.now(UTC)

    result = login_user(
        session,
        " Potato ",
        PASSWORD,
        settings,
        user_agent="a" * 600,
    )

    assert result.expires_in == settings.access_token_minutes * 60
    assert result.session.user_id == user.id
    assert result.session.refresh_token_hash == digest_refresh_token(
        result.refresh_token
    )
    assert result.session.user_agent == "a" * 512
    assert before + timedelta(days=30) <= result.session.expires_at.replace(
        tzinfo=UTC
    ) <= datetime.now(UTC) + timedelta(days=30)


def test_unknown_user_runs_dummy_verification_and_uses_unified_failure(
    session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.auth.verify_dummy_password", lambda value: calls.append(value)
    )

    with pytest.raises(InvalidCredentials):
        login_user(session, "missing", PASSWORD, settings, user_agent=None)

    assert calls == [PASSWORD]
    assert session.scalar(select(AuthSession.id)) is None


def test_wrong_password_and_inactive_account_share_the_same_failure(
    session: Session, settings: Settings
) -> None:
    create_account(session, "potato", PASSWORD)

    with pytest.raises(InvalidCredentials):
        login_user(session, "potato", "wrong password", settings, user_agent=None)

    deactivate_account(session, "potato")
    with pytest.raises(InvalidCredentials):
        login_user(session, "potato", PASSWORD, settings, user_agent=None)


def test_refresh_rotates_digest_without_extending_session(
    session: Session, settings: Settings
) -> None:
    create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent="pytest")
    original_expiry = login.session.expires_at

    rotated = refresh_session(session, login.refresh_token, settings)

    assert rotated.refresh_token != login.refresh_token
    assert rotated.session.expires_at == original_expiry
    assert rotated.session.refresh_token_hash == digest_refresh_token(
        rotated.refresh_token
    )


def test_refresh_replay_revokes_the_corresponding_session(
    session: Session, settings: Settings
) -> None:
    create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent=None)
    refresh_session(session, login.refresh_token, settings)

    with pytest.raises(RefreshReplayed):
        refresh_session(session, login.refresh_token, settings)

    persisted_revoked_at = session.scalar(
        select(AuthSession.revoked_at).where(AuthSession.id == login.session.id)
    )
    assert persisted_revoked_at is not None


def test_invalid_refresh_token_is_rejected_without_database_changes(
    session: Session, settings: Settings
) -> None:
    create_account(session, "potato", PASSWORD)

    with pytest.raises(RefreshRequired):
        refresh_session(session, "not-a-token", settings)

    assert session.scalar(select(AuthSession.id)) is None


def test_refresh_rejects_an_inactive_user_even_if_session_was_not_pre_revoked(
    session: Session, settings: Settings
) -> None:
    user = create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent=None)
    # 模拟维护脚本绕过账号服务直接停用，认证服务仍必须独立守住活动账号边界。
    user.is_active = False
    session.commit()

    with pytest.raises(RefreshRequired):
        refresh_session(session, login.refresh_token, settings)

    revoked_at = session.scalar(
        select(AuthSession.revoked_at).where(AuthSession.id == login.session.id)
    )
    assert revoked_at is not None


def test_logout_is_idempotent_for_missing_invalid_and_repeated_tokens(
    session: Session, settings: Settings
) -> None:
    create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent=None)

    logout_session(session, None, settings)
    logout_session(session, "not-a-token", settings)
    logout_session(session, login.refresh_token, settings)
    logout_session(session, login.refresh_token, settings)

    persisted_revoked_at = session.scalar(
        select(AuthSession.revoked_at).where(AuthSession.id == login.session.id)
    )
    assert persisted_revoked_at is not None


def test_access_token_authentication_reloads_active_user(
    session: Session, settings: Settings
) -> None:
    user = create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent=None)

    assert authenticate_access_token(session, login.access_token, settings).id == user.id

    deactivate_account(session, "potato")
    with pytest.raises(AuthenticationRequired):
        authenticate_access_token(session, login.access_token, settings)
    with pytest.raises(AuthenticationRequired):
        authenticate_access_token(session, "not-a-token", settings)
