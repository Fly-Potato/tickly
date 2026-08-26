from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.services.accounts as account_services
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import AuthSession, McpToken, User
from app.services.accounts import (
    AccountAlreadyExists,
    AccountNotFound,
    InvalidCurrentPassword,
    PasswordChangeFailed,
    PasswordUnchanged,
    change_password,
    change_own_password,
    create_account,
    deactivate_account,
    revoke_all_sessions,
)
from app.core.security import verify_password


PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct password"


def create_test_database(
    tmp_path: Path, filename: str = "accounts.db"
) -> tuple[Engine, sessionmaker[Session], str]:
    """创建完成 migration 的文件数据库，供单 Session 与真实并发测试复用。"""

    database_path = tmp_path / filename
    database_url = f"sqlite:///{database_path}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    return engine, create_session_factory(engine), database_url


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine, session_factory, _ = create_test_database(tmp_path)

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


def test_create_account_allows_distinct_users_and_rejects_normalized_duplicate(
    session: Session,
) -> None:
    user = create_account(session, " Potato ", PASSWORD)
    second = create_account(session, "second", NEW_PASSWORD)

    assert user.username == "potato"
    assert second.username == "second"
    assert verify_password(PASSWORD, user.password_hash)
    with pytest.raises(AccountAlreadyExists):
        create_account(session, " POTATO ", NEW_PASSWORD)


def test_concurrent_duplicate_username_rolls_back_and_reuses_losing_session(
    tmp_path: Path,
) -> None:
    engine, session_factory, database_url = create_test_database(
        tmp_path, "concurrent-accounts.db"
    )
    first_write_held = Event()
    second_insert_attempted = Event()
    release_first_commit = Event()
    insert_count_lock = Lock()
    user_insert_count = 0

    def is_user_insert(statement: str) -> bool:
        return statement.lstrip().upper().startswith("INSERT INTO USERS")

    def observe_user_insert_attempt(
        _connection: object,
        _cursor: object,
        statement: str,
        *_: object,
    ) -> None:
        nonlocal user_insert_count
        if not is_user_insert(statement):
            return
        with insert_count_lock:
            user_insert_count += 1
            call_number = user_insert_count
        if call_number == 2:
            assert first_write_held.is_set(), "第二次 INSERT 发生时首个写锁尚未持有"
            second_insert_attempted.set()

    def hold_first_user_write(
        _connection: object,
        _cursor: object,
        statement: str,
        *_: object,
    ) -> None:
        if not is_user_insert(statement) or first_write_held.is_set():
            return
        # DBAPI 已完成首个 INSERT，但 create_account 尚未 commit，此时真实写锁仍在。
        first_write_held.set()
        assert release_first_commit.wait(timeout=5), "等待释放首个账号写事务超时"

    def create_concurrently(
        raw_username: str,
    ) -> tuple[str, str, str]:
        with session_factory() as worker_session:
            try:
                created = create_account(worker_session, raw_username, PASSWORD)
            except AccountAlreadyExists as error:
                failure_text = f"{error!s}\n{error!r}"
                recovered = create_account(
                    worker_session, "recovered", NEW_PASSWORD
                )
                return "duplicate", failure_text, recovered.username
            return "created", "", created.username

    event.listen(engine, "before_cursor_execute", observe_user_insert_attempt)
    event.listen(engine, "after_cursor_execute", hold_first_user_write)
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        first_future = executor.submit(create_concurrently, " Potato ")
        assert first_write_held.wait(timeout=5), "首个账号 INSERT 未取得 SQLite 写锁"
        second_future = executor.submit(create_concurrently, "POTATO")
        assert second_insert_attempted.wait(timeout=5), (
            "第二个账号 INSERT 未在首个写事务持锁时到达 DBAPI 驱动"
        )
        assert not first_future.done(), "第二次 INSERT 到达前首个写事务已提前结束"
        release_first_commit.set()
        futures = [first_future, second_future]
        outcomes = [future.result(timeout=15) for future in futures]

        assert sorted(outcome for outcome, _, _ in outcomes) == [
            "created",
            "duplicate",
        ]
        _, failure_text, recovered_username = next(
            result for result in outcomes if result[0] == "duplicate"
        )
        assert recovered_username == "recovered"

        with session_factory() as verification_session:
            normalized_count = verification_session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.username == "potato")
            )
            users = list(
                verification_session.scalars(
                    select(User).order_by(User.username)
                )
            )

        assert normalized_count == 1
        assert [user.username for user in users] == ["potato", "recovered"]
        for secret in (
            PASSWORD,
            NEW_PASSWORD,
            database_url,
            *(user.password_hash for user in users),
        ):
            assert secret not in failure_text
        lowered_failure = failure_text.lower()
        assert "sql" not in lowered_failure
        assert "users.username" not in lowered_failure
        assert "unique" not in lowered_failure
        assert first_write_held.is_set()
        assert second_insert_attempted.is_set()
        assert user_insert_count == 3
    finally:
        release_first_commit.set()
        executor.shutdown(wait=True)
        event.remove(engine, "after_cursor_execute", hold_first_user_write)
        event.remove(engine, "before_cursor_execute", observe_user_insert_attempt)
        engine.dispose()


def test_change_password_revokes_all_active_sessions_in_one_committed_state(
    session: Session,
) -> None:
    user = create_account(session, "potato", PASSWORD)
    first_session = add_active_session(session, user.id, "password-change-first")
    second_session = add_active_session(session, user.id, "password-change-second")

    changed = change_password(session, " Potato ", NEW_PASSWORD)
    session.refresh(first_session)
    session.refresh(second_session)

    assert verify_password(NEW_PASSWORD, changed.password_hash)
    assert not verify_password(PASSWORD, changed.password_hash)
    assert changed.auth_version == 2
    assert first_session.revoked_at is not None
    assert second_session.revoked_at is not None


def test_change_own_password_verifies_current_and_preserves_mcp_tokens(
    session: Session,
) -> None:
    user = create_account(session, "potato", PASSWORD)
    auth_session = add_active_session(session, user.id, "self-service")
    mcp_token = McpToken(
        user_id=user.id,
        name="自动化",
        token_hash="a" * 64,
    )
    session.add(mcp_token)
    session.commit()

    changed = change_own_password(session, user, PASSWORD, NEW_PASSWORD)
    session.refresh(auth_session)
    session.refresh(mcp_token)

    assert changed.auth_version == 2
    assert verify_password(NEW_PASSWORD, changed.password_hash)
    assert auth_session.revoked_at is not None
    assert mcp_token.revoked_at is None


@pytest.mark.parametrize(
    ("current_password", "new_password", "expected_error"),
    [
        ("wrong current secret", NEW_PASSWORD, InvalidCurrentPassword),
        (PASSWORD, PASSWORD, PasswordUnchanged),
    ],
)
def test_change_own_password_rejects_invalid_or_unchanged_password_without_mutation(
    session: Session,
    current_password: str,
    new_password: str,
    expected_error: type[Exception],
) -> None:
    user = create_account(session, "potato", PASSWORD)
    auth_session = add_active_session(session, user.id, "self-service-failed")
    original_hash = user.password_hash
    original_version = user.auth_version

    with pytest.raises(expected_error):
        change_own_password(session, user, current_password, new_password)

    assert user.password_hash == original_hash
    assert user.auth_version == original_version
    assert auth_session.revoked_at is None
    assert verify_password(PASSWORD, user.password_hash)


def test_change_own_password_sanitizes_hash_failure_without_exception_context(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "potato", PASSWORD)

    def fail_hash(_: str) -> str:
        raise RuntimeError("含敏感实现细节的散列失败")

    monkeypatch.setattr(account_services, "hash_password", fail_hash)

    with pytest.raises(PasswordChangeFailed) as captured:
        change_own_password(session, user, PASSWORD, NEW_PASSWORD)

    assert captured.value.stage == "password_hash"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_change_own_password_sanitizes_database_failure_without_exception_context(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "potato", PASSWORD)
    auth_session = add_active_session(session, user.id, "sanitized-database")

    def fail_commit() -> None:
        raise IntegrityError(
            "UPDATE users SET password_hash=:secret",
            {"secret": "敏感散列参数"},
            RuntimeError("敏感数据库失败"),
        )

    monkeypatch.setattr(session, "commit", fail_commit)

    with pytest.raises(PasswordChangeFailed) as captured:
        change_own_password(session, user, PASSWORD, NEW_PASSWORD)

    assert captured.value.stage == "database"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert verify_password(PASSWORD, user.password_hash)
    assert user.auth_version == 1
    assert auth_session.revoked_at is None


def test_change_own_password_sanitizes_sqlalchemy_rollback_failure(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "potato", PASSWORD)

    def fail_commit() -> None:
        raise IntegrityError(
            "UPDATE users SET password_hash=:commit_secret",
            {"commit_secret": "敏感提交参数"},
            RuntimeError("敏感提交失败"),
        )

    def fail_rollback() -> None:
        raise IntegrityError(
            "ROLLBACK /* rollback_secret */",
            {"rollback_secret": "敏感回滚参数"},
            RuntimeError("敏感回滚失败"),
        )

    monkeypatch.setattr(session, "commit", fail_commit)
    monkeypatch.setattr(session, "rollback", fail_rollback)

    with pytest.raises(PasswordChangeFailed) as captured:
        change_own_password(session, user, PASSWORD, NEW_PASSWORD)

    assert captured.value.stage == "database"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_change_own_password_does_not_misclassify_programmer_error(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "potato", PASSWORD)

    def fail_programmer_boundary(_: User, __: str) -> None:
        raise AttributeError("deliberate programmer error")

    monkeypatch.setattr(
        account_services,
        "_apply_password_change",
        fail_programmer_boundary,
    )

    with pytest.raises(AttributeError, match="deliberate programmer error"):
        change_own_password(session, user, PASSWORD, NEW_PASSWORD)


def test_change_password_commit_failure_restores_objects_and_allows_retry(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "potato", PASSWORD)
    first_session = add_active_session(session, user.id, "rollback-first")
    second_session = add_active_session(session, user.id, "rollback-second")

    def fail_commit() -> None:
        # 注入点必须位于密码、版本和全部会话撤销均已进入待提交状态之后。
        assert verify_password(NEW_PASSWORD, user.password_hash)
        assert user.auth_version == 2
        pending_revocations = list(
            session.scalars(
                select(AuthSession.revoked_at).where(
                    AuthSession.user_id == user.id
                )
            )
        )
        assert len(pending_revocations) == 2
        assert all(revoked_at is not None for revoked_at in pending_revocations)
        raise IntegrityError("强制提交失败", {}, RuntimeError("测试事务回滚"))

    with monkeypatch.context() as commit_failure:
        commit_failure.setattr(session, "commit", fail_commit)
        with pytest.raises(IntegrityError):
            change_password(session, "potato", NEW_PASSWORD)

    # 先读取原有 ORM 对象，证明 rollback 已恢复身份映射中的可见状态。
    assert verify_password(PASSWORD, user.password_hash)
    assert not verify_password(NEW_PASSWORD, user.password_hash)
    assert user.auth_version == 1
    assert first_session.revoked_at is None
    assert second_session.revoked_at is None

    persisted_user = session.scalar(select(User).where(User.id == user.id))
    persisted_revocations = list(
        session.scalars(
            select(AuthSession.revoked_at)
            .where(AuthSession.user_id == user.id)
            .order_by(AuthSession.id)
        )
    )
    assert persisted_user is user
    assert persisted_revocations == [None, None]

    changed = change_password(session, "potato", NEW_PASSWORD)
    assert changed.auth_version == 2
    assert verify_password(NEW_PASSWORD, changed.password_hash)
    assert all(
        revoked_at is not None
        for revoked_at in session.scalars(
            select(AuthSession.revoked_at).where(AuthSession.user_id == user.id)
        )
    )


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
