from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import create_engine_for_settings, create_session_factory
from app.models import AuthSession
from app.services.accounts import (
    AccountAlreadyExists,
    AccountNotFound,
    change_password,
    create_account,
    deactivate_account,
    revoke_all_sessions,
)
from app.core.security import verify_password


PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct password"


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database_path = tmp_path / "accounts.db"
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


def add_active_session(session: Session, user_id: str, suffix: str) -> AuthSession:
    auth_session = AuthSession(
        user_id=user_id,
        refresh_token_hash=f"digest-{suffix}",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        user_agent="pytest",
    )
    session.add(auth_session)
    session.commit()
    return auth_session


def test_create_account_normalizes_username_and_refuses_second_user(
    session: Session,
) -> None:
    user = create_account(session, " Potato ", PASSWORD)

    assert user.username == "potato"
    assert verify_password(PASSWORD, user.password_hash)
    with pytest.raises(AccountAlreadyExists):
        create_account(session, "second", NEW_PASSWORD)


def test_change_password_revokes_all_active_sessions_in_one_committed_state(
    session: Session,
) -> None:
    user = create_account(session, "potato", PASSWORD)
    auth_session = add_active_session(session, user.id, "password-change")

    changed = change_password(session, " Potato ", NEW_PASSWORD)
    session.refresh(auth_session)

    assert verify_password(NEW_PASSWORD, changed.password_hash)
    assert not verify_password(PASSWORD, changed.password_hash)
    assert auth_session.revoked_at is not None


def test_deactivate_account_revokes_sessions_without_deleting_the_user(
    session: Session,
) -> None:
    user = create_account(session, "potato", PASSWORD)
    auth_session = add_active_session(session, user.id, "deactivate")

    deactivated = deactivate_account(session, "POTATO")
    session.refresh(auth_session)

    assert deactivated.is_active is False
    assert auth_session.revoked_at is not None


def test_revoke_all_sessions_only_updates_active_rows(session: Session) -> None:
    user = create_account(session, "potato", PASSWORD)
    first = add_active_session(session, user.id, "first")
    second = add_active_session(session, user.id, "second")

    assert revoke_all_sessions(session, "potato") == 2
    session.refresh(first)
    session.refresh(second)
    assert first.revoked_at is not None
    assert second.revoked_at is not None
    assert revoke_all_sessions(session, "potato") == 0


def test_account_operations_reject_unknown_username(session: Session) -> None:
    with pytest.raises(AccountNotFound):
        change_password(session, "missing", NEW_PASSWORD)
    with pytest.raises(AccountNotFound):
        deactivate_account(session, "missing")
    with pytest.raises(AccountNotFound):
        revoke_all_sessions(session, "missing")

    assert session.scalar(select(AuthSession.id)) is None
