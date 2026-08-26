from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Environment, Settings
from app.core.security import decode_token, digest_refresh_token
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import AuthSession
from app.services.accounts import change_password, create_account, deactivate_account
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


def test_legacy_refresh_rotates_after_auth_version_changes_and_issues_current_access(
    session: Session, settings: Settings
) -> None:
    user = create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent="pytest")
    assert decode_token(login.refresh_token, "refresh", settings).ver is None
    user.auth_version = 2
    session.commit()

    rotated = refresh_session(session, login.refresh_token, settings)

    assert decode_token(rotated.access_token, "access", settings).ver == 2
    assert authenticate_access_token(session, rotated.access_token, settings).id == user.id


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


def test_access_token_authentication_rejects_stale_auth_version(
    session: Session, settings: Settings
) -> None:
    user = create_account(session, "potato", PASSWORD)
    login = login_user(session, "potato", PASSWORD, settings, user_agent=None)
    user.auth_version = 2
    session.commit()

    with pytest.raises(AuthenticationRequired):
        authenticate_access_token(session, login.access_token, settings)


def test_password_change_invalidates_old_credentials_across_sessions(
    tmp_path: Path, settings: Settings
) -> None:
    database_path = tmp_path / "password-rotation-flow.db"
    database_url = f"sqlite:///{database_path}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    session_factory = create_session_factory(engine)

    try:
        with session_factory() as login_session:
            create_account(login_session, "potato", PASSWORD)
            old_login = login_user(
                login_session,
                "potato",
                PASSWORD,
                settings,
                user_agent="pytest",
            )
            old_access = old_login.access_token
            old_refresh = old_login.refresh_token

        with session_factory() as maintenance_session:
            changed = change_password(
                maintenance_session, "potato", "new correct password"
            )
            current_version = changed.auth_version

        with session_factory() as stale_credentials_session:
            with pytest.raises(AuthenticationRequired):
                authenticate_access_token(
                    stale_credentials_session, old_access, settings
                )
            with pytest.raises((RefreshRequired, RefreshReplayed)):
                refresh_session(stale_credentials_session, old_refresh, settings)

        with session_factory() as new_login_session:
            new_login = login_user(
                new_login_session,
                "potato",
                "new correct password",
                settings,
                user_agent="pytest",
            )
            assert (
                decode_token(new_login.access_token, "access", settings).ver
                == current_version
            )
    finally:
        engine.dispose()
