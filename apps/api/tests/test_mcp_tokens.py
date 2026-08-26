from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import base64
import hashlib
from pathlib import Path
import re
from threading import Barrier, Event, Lock, local
from uuid import UUID

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import app.services.mcp_tokens as mcp_token_services
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import McpToken, User
from app.services.accounts import create_account
from app.services.mcp_tokens import (
    McpAuthenticationRequired,
    McpTokenNotFound,
    McpTokenPersistenceFailed,
    McpTokenValidationError,
    authenticate_mcp_token,
    create_mcp_token,
    list_mcp_tokens,
    mcp_token_status,
    revoke_mcp_token,
)


PASSWORD = "correct horse battery staple"
TOKEN_PATTERN = re.compile(
    r"^tickly_mcp_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})\.([A-Za-z0-9_-]{43})$"
)


def create_test_database(
    tmp_path: Path,
) -> tuple[Engine, sessionmaker[Session]]:
    """创建完成 migration 的临时数据库，验证真实约束与事务行为。"""

    database_url = f"sqlite:///{tmp_path / 'mcp-tokens.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    return engine, create_session_factory(engine)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine, session_factory = create_test_database(tmp_path)
    with session_factory() as database_session:
        yield database_session
    engine.dispose()


def test_create_returns_raw_once_and_persists_only_sha256_digest(
    session: Session,
) -> None:
    user = create_account(session, "first", PASSWORD)

    issued = create_mcp_token(session, user.id, "家中 Codex", 365)

    match = TOKEN_PATTERN.fullmatch(issued.raw_token)
    assert match is not None
    assert match.group(1) == issued.record.id
    assert str(UUID(match.group(1))) == match.group(1)
    decoded_secret = base64.urlsafe_b64decode(match.group(2) + "=")
    assert len(decoded_secret) == 32
    expected_digest = hashlib.sha256(issued.raw_token.encode("utf-8")).hexdigest()
    assert issued.record.token_hash == expected_digest
    assert issued.raw_token not in issued.record.token_hash
    assert not hasattr(issued.record, "raw_token")

    stored = session.execute(
        text("SELECT token_hash, name FROM mcp_tokens WHERE id = :id"),
        {"id": issued.record.id},
    ).one()
    assert stored.token_hash == expected_digest
    assert issued.raw_token not in " ".join(stored)
    assert issued.raw_token not in repr(issued)
    assert expected_digest not in repr(issued)


@pytest.mark.parametrize("expires_in_days", [90, 365, None])
def test_create_accepts_only_supported_expiry_choices_and_uses_utc(
    session: Session,
    expires_in_days: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(mcp_token_services, "utc_now", lambda: now)
    user = create_account(session, f"user{expires_in_days}", PASSWORD)

    issued = create_mcp_token(
        session, user.id, "有效期验证", expires_in_days
    )

    expected = (
        now + timedelta(days=expires_in_days)
        if expires_in_days is not None
        else None
    )
    assert issued.record.created_at == now
    assert issued.record.expires_at == expected


@pytest.mark.parametrize(
    "expires_in_days",
    [-1, 0, 1, 89, 91, 364, 366, 90.0, "90", True],
    ids=[
        "负数",
        "零",
        "一天",
        "八十九天",
        "九十一天",
        "三百六十四天",
        "三百六十六天",
        "浮点数",
        "字符串",
        "布尔值",
    ],
)
def test_create_rejects_invalid_expiry_service_callers(
    session: Session, expires_in_days: object
) -> None:
    user = create_account(session, "first", PASSWORD)

    with pytest.raises(ValueError, match="90, 365, or None"):
        create_mcp_token(session, user.id, "无效有效期", expires_in_days)  # type: ignore[arg-type]

    assert list_mcp_tokens(session, user.id) == []


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        (123, "type"),
        (" \t\r\n ", "empty"),
        ("名" * 65, "too_long"),
        ("\0开头", "nul"),
        ("中间\0字符", "nul"),
        ("结尾\0", "nul"),
    ],
    ids=[
        "非字符串",
        "去除空白后为空",
        "超过六十四字符",
        "空字符位于开头",
        "空字符位于中间",
        "空字符位于结尾",
    ],
)
def test_create_rejects_invalid_names_before_secret_generation(
    session: Session,
    name: object,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "first", PASSWORD)

    def unexpected_uuid() -> UUID:
        raise AssertionError("非法名称不得生成 Token locator")

    def unexpected_secret(_: int) -> str:
        raise AssertionError("非法名称不得生成 Token secret")

    monkeypatch.setattr(mcp_token_services, "uuid4", unexpected_uuid)
    monkeypatch.setattr(
        mcp_token_services.secrets,
        "token_urlsafe",
        unexpected_secret,
    )

    with pytest.raises(McpTokenValidationError) as captured:
        create_mcp_token(session, user.id, name, None)  # type: ignore[arg-type]

    assert captured.value.field == "name"
    assert captured.value.reason == reason
    assert str(name) not in str(captured.value)
    assert list_mcp_tokens(session, user.id) == []


def test_invalid_name_keeps_session_reusable(session: Session) -> None:
    user = create_account(session, "first", PASSWORD)

    with pytest.raises(McpTokenValidationError):
        create_mcp_token(session, user.id, "   ", None)

    issued = create_mcp_token(session, user.id, "有效名称", None)
    assert [item.id for item in list_mcp_tokens(session, user.id)] == [
        issued.record.id
    ]


def test_multiple_reused_names_are_ordered_and_isolated_by_owner(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_user = create_account(session, "first", PASSWORD)
    second_user = create_account(session, "second", PASSWORD)
    fixed_now = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    token_ids = iter(
        [
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
            UUID("33333333-3333-4333-8333-333333333333"),
        ]
    )
    monkeypatch.setattr(mcp_token_services, "utc_now", lambda: fixed_now)
    monkeypatch.setattr(mcp_token_services, "uuid4", lambda: next(token_ids))

    first = create_mcp_token(session, first_user.id, "Codex", 365)
    second = create_mcp_token(session, first_user.id, "Codex", None)
    other = create_mcp_token(session, second_user.id, "Codex", 90)

    assert [item.id for item in list_mcp_tokens(session, first_user.id)] == [
        second.record.id,
        first.record.id,
    ]
    assert [item.id for item in list_mcp_tokens(session, second_user.id)] == [
        other.record.id
    ]


@pytest.mark.parametrize(
    "raw_token",
    [
        "",
        "wrong_00000000-0000-4000-8000-000000000000." + "a" * 43,
        "tickly_mcp_00000000-0000-4000-8000-000000000000",
        "tickly_mcp_00000000-0000-4000-8000-000000000000.." + "a" * 43,
        "tickly_mcp_00000000-0000-4000-8000-000000000000.",
        "tickly_mcp_00000000-0000-4000-8000-000000000000." + "a" * 42,
        "tickly_mcp_00000000-0000-4000-8000-000000000000." + "a" * 44,
        "tickly_mcp_00000000-0000-4000-8000-000000000000." + "a" * 42 + "=",
        "tickly_mcp_00000000-0000-4000-8000-000000000000." + "a" * 42 + ".",
        "tickly_mcp_00000000000040008000000000000000." + "a" * 43,
        "tickly_mcp_{00000000-0000-4000-8000-000000000000}." + "a" * 43,
        "tickly_mcp_00000000-0000-4000-8000-00000000000A." + "a" * 43,
        "x" * 1_000_000,
    ],
    ids=[
        "空字符串",
        "错误前缀",
        "缺少分隔符",
        "额外分隔符",
        "空secret",
        "secret过短",
        "secret过长",
        "secret包含填充符",
        "secret包含非url字符",
        "UUID缺少连字符",
        "UUID包含花括号",
        "UUID大小写不规范",
        "超大输入",
    ],
)
def test_authentication_rejects_malformed_tokens_without_lookup_or_hash(
    session: Session,
    raw_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons: list[tuple[str, str]] = []
    original_compare = mcp_token_services.hmac.compare_digest

    def unexpected_get(*_: object, **__: object) -> object:
        raise AssertionError("格式错误的 Token 不得查询数据库")

    def unexpected_digest(_: str) -> str:
        raise AssertionError("格式错误的 Token 不得参与 SHA-256 散列")

    def record_compare(actual: str, expected: str) -> bool:
        comparisons.append((actual, expected))
        return original_compare(actual, expected)

    monkeypatch.setattr(session, "get", unexpected_get)
    monkeypatch.setattr(mcp_token_services, "_digest", unexpected_digest)
    monkeypatch.setattr(
        mcp_token_services.hmac, "compare_digest", record_compare
    )

    with pytest.raises(McpAuthenticationRequired):
        authenticate_mcp_token(session, raw_token)

    assert comparisons == [
        (mcp_token_services._DUMMY_DIGEST, mcp_token_services._DUMMY_DIGEST)
    ]
    assert re.fullmatch(r"[0-9a-f]{64}", comparisons[0][1])


def test_wrong_digest_and_missing_locator_share_the_same_auth_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "测试", None)
    wrong_digest_token = issued.raw_token[:-1] + (
        "A" if issued.raw_token[-1] != "A" else "B"
    )
    missing_token = (
        "tickly_mcp_ffffffff-ffff-4fff-8fff-ffffffffffff." + "a" * 43
    )

    lookups: list[tuple[type[object], object]] = []
    comparisons: list[tuple[str, str]] = []
    original_get = session.get
    original_compare = mcp_token_services.hmac.compare_digest

    def record_get(entity: type[object], identifier: object) -> object | None:
        lookups.append((entity, identifier))
        return original_get(entity, identifier)

    def record_compare(actual: str, expected: str) -> bool:
        comparisons.append((actual, expected))
        return original_compare(actual, expected)

    monkeypatch.setattr(session, "get", record_get)
    monkeypatch.setattr(
        mcp_token_services.hmac, "compare_digest", record_compare
    )

    captured: list[McpAuthenticationRequired] = []
    for raw_token in (wrong_digest_token, missing_token):
        with pytest.raises(McpAuthenticationRequired) as error:
            authenticate_mcp_token(session, raw_token)
        captured.append(error.value)

    assert [str(error) for error in captured] == ["", ""]
    assert [repr(error) for error in captured] == [
        "McpAuthenticationRequired()",
        "McpAuthenticationRequired()",
    ]
    assert lookups == [
        (McpToken, issued.record.id),
        (McpToken, "ffffffff-ffff-4fff-8fff-ffffffffffff"),
    ]
    assert comparisons == [
        (
            hashlib.sha256(wrong_digest_token.encode("utf-8")).hexdigest(),
            issued.record.token_hash,
        ),
        (
            hashlib.sha256(missing_token.encode("utf-8")).hexdigest(),
            mcp_token_services._DUMMY_DIGEST,
        ),
    ]


def test_authentication_resolves_only_the_record_owner(session: Session) -> None:
    first = create_account(session, "first", PASSWORD)
    second = create_account(session, "second", PASSWORD)
    issued = create_mcp_token(session, second.id, "公司 Codex", 90)

    authenticated = authenticate_mcp_token(session, issued.raw_token)

    assert authenticated.id == second.id
    assert authenticated.id != first.id


@pytest.mark.parametrize(
    "state", ["expired", "revoked", "inactive"], ids=["过期", "撤销", "账号停用"]
)
def test_authentication_fails_closed_for_terminal_states(
    session: Session, state: str
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "测试", None)
    if state == "expired":
        issued.record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif state == "revoked":
        issued.record.revoked_at = datetime.now(UTC)
    else:
        user.is_active = False
    session.commit()

    with pytest.raises(McpAuthenticationRequired):
        authenticate_mcp_token(session, issued.raw_token)


def test_authentication_rejects_a_missing_owner_without_client_user_id(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "孤立账号", None)
    original_get = session.get

    def hide_owner(entity: type[object], identifier: object) -> object | None:
        if entity is User:
            return None
        return original_get(entity, identifier)

    monkeypatch.setattr(session, "get", hide_owner)

    with pytest.raises(McpAuthenticationRequired):
        authenticate_mcp_token(session, issued.raw_token)


@pytest.mark.parametrize(
    "failure_stage", ["record", "owner"], ids=["Token记录查询", "所有者查询"]
)
def test_lookup_database_failures_are_sanitized_and_session_is_reusable(
    session: Session,
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "查询失败", None)
    original_get = session.get
    original_rollback = session.rollback
    rollback_count = 0

    def fail_selected_lookup(
        entity: type[object], identifier: object
    ) -> object | None:
        should_fail = (
            failure_stage == "record" and entity is McpToken
        ) or (failure_stage == "owner" and entity is User)
        if should_fail:
            raise SQLAlchemyError(
                f"数据库失败 {issued.raw_token} {issued.record.token_hash}"
            )
        return original_get(entity, identifier)

    def record_rollback() -> None:
        nonlocal rollback_count
        rollback_count += 1
        original_rollback()

    with monkeypatch.context() as failure:
        failure.setattr(session, "get", fail_selected_lookup)
        failure.setattr(session, "rollback", record_rollback)
        with pytest.raises(McpTokenPersistenceFailed) as captured:
            authenticate_mcp_token(session, issued.raw_token)

    assert captured.value.stage == "lookup"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert issued.raw_token not in str(captured.value)
    assert issued.record.token_hash not in repr(captured.value)
    assert rollback_count == 1
    assert authenticate_mcp_token(session, issued.raw_token).id == user.id


def test_status_uses_utc_for_naive_sqlite_values_and_exact_expiry() -> None:
    exact_now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    record = McpToken(
        user_id="00000000-0000-4000-8000-000000000000",
        name="状态",
        token_hash="a" * 64,
        expires_at=exact_now.replace(tzinfo=None),
    )

    assert mcp_token_status(record, exact_now) == "expired"
    record.revoked_at = exact_now.replace(tzinfo=None)
    assert mcp_token_status(record, exact_now) == "revoked"


def test_first_authentication_writes_last_used_then_throttles_until_one_hour(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "遥测", None)
    clock = [datetime(2026, 8, 26, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(mcp_token_services, "utc_now", lambda: clock[0])

    authenticate_mcp_token(session, issued.raw_token)
    first_used_at = issued.record.last_used_at
    assert first_used_at == clock[0]

    clock[0] += timedelta(minutes=59, seconds=59)
    authenticate_mcp_token(session, issued.raw_token)
    assert issued.record.last_used_at == first_used_at

    clock[0] = first_used_at + timedelta(hours=1)
    authenticate_mcp_token(session, issued.raw_token)
    assert issued.record.last_used_at == clock[0]


def test_naive_sqlite_last_used_is_treated_as_utc_for_throttling(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "SQLite 遥测", None)
    now = datetime(2026, 8, 26, 13, 0, tzinfo=UTC)
    issued.record.last_used_at = (now - timedelta(minutes=30)).replace(tzinfo=None)
    session.commit()
    monkeypatch.setattr(mcp_token_services, "utc_now", lambda: now)

    def unexpected_commit() -> None:
        raise AssertionError("一小时内不得更新遥测")

    monkeypatch.setattr(session, "commit", unexpected_commit)

    assert authenticate_mcp_token(session, issued.raw_token).id == user.id
    assert issued.record.last_used_at == now - timedelta(minutes=30)


def test_fresh_telemetry_does_not_update_or_hold_a_sqlite_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(tmp_path)
    now = datetime(2026, 8, 26, 13, 0, tzinfo=UTC)
    telemetry_updates = 0

    with session_factory() as setup_session:
        user = create_account(setup_session, "first", PASSWORD)
        issued = create_mcp_token(setup_session, user.id, "新鲜遥测", None)
        issued.record.last_used_at = (now - timedelta(minutes=30)).replace(
            tzinfo=None
        )
        setup_session.commit()
        raw_token = issued.raw_token
        user_id = user.id

    def count_telemetry_updates(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal telemetry_updates
        normalized = " ".join(statement.upper().split())
        if normalized.startswith("UPDATE MCP_TOKENS SET LAST_USED_AT"):
            telemetry_updates += 1

    event.listen(engine, "before_cursor_execute", count_telemetry_updates)
    monkeypatch.setattr(mcp_token_services, "utc_now", lambda: now)

    try:
        with session_factory() as authentication_session:
            assert (
                authenticate_mcp_token(authentication_session, raw_token).id
                == user_id
            )
            with session_factory() as writer_session:
                writer_session.connection().exec_driver_sql(
                    "PRAGMA busy_timeout=0"
                )
                writer = writer_session.get(User, user_id)
                assert writer is not None
                writer.timezone = "UTC"
                writer_session.commit()
        assert telemetry_updates == 0
    finally:
        event.remove(engine, "before_cursor_execute", count_telemetry_updates)
        engine.dispose()


def test_concurrent_authentication_conditionally_updates_telemetry_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(tmp_path)
    winner_now = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)
    loser_now = winner_now + timedelta(seconds=1)
    thread_state = local()
    both_updates_ready = Barrier(2)
    winner_committed = Event()
    loser_returned = Event()
    release_loser = Event()
    observations: list[tuple[bool, int]] = []
    observation_lock = Lock()

    with session_factory() as setup_session:
        user = create_account(setup_session, "first", PASSWORD)
        issued = create_mcp_token(setup_session, user.id, "并发遥测", None)
        issued.record.last_used_at = (
            winner_now - timedelta(hours=2)
        ).replace(tzinfo=None)
        setup_session.commit()
        raw_token = issued.raw_token
        user_id = user.id

    def is_last_used_update(statement: str) -> bool:
        normalized = " ".join(statement.upper().split())
        return normalized.startswith(
            "UPDATE MCP_TOKENS SET LAST_USED_AT"
        )

    def coordinate_updates(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not is_last_used_update(statement):
            return
        both_updates_ready.wait(timeout=10)
        if not thread_state.is_winner:
            assert winner_committed.wait(timeout=10)

    def observe_rowcount(
        _connection: object,
        cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not is_last_used_update(statement):
            return
        with observation_lock:
            observations.append(
                (thread_state.is_winner, cursor.rowcount)  # type: ignore[attr-defined]
            )

    event.listen(engine, "before_cursor_execute", coordinate_updates)
    event.listen(engine, "after_cursor_execute", observe_rowcount)
    monkeypatch.setattr(
        mcp_token_services,
        "utc_now",
        lambda: thread_state.now,
    )

    def authenticate_at(now: datetime, *, is_winner: bool) -> str:
        thread_state.now = now
        thread_state.is_winner = is_winner
        authentication_session = session_factory()
        try:
            authenticated_id = authenticate_mcp_token(
                authentication_session, raw_token
            ).id
            if not is_winner:
                loser_returned.set()
                assert release_loser.wait(timeout=10)
            return authenticated_id
        finally:
            if is_winner:
                winner_committed.set()
            authentication_session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            winner = executor.submit(
                authenticate_at, winner_now, is_winner=True
            )
            loser = executor.submit(
                authenticate_at, loser_now, is_winner=False
            )
            assert winner.result(timeout=15) == user_id
            assert loser_returned.wait(timeout=15)
            with session_factory() as writer_session:
                writer_session.connection().exec_driver_sql(
                    "PRAGMA busy_timeout=0"
                )
                writer = writer_session.get(User, user_id)
                assert writer is not None
                writer.timezone = "UTC"
                writer_session.commit()
            release_loser.set()
            assert loser.result(timeout=15) == user_id

        assert sorted(observations) == [(False, 0), (True, 1)]
        with session_factory() as verification_session:
            final_last_used_at = verification_session.scalar(
                select(McpToken.last_used_at).where(
                    McpToken.token_hash
                    == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                )
            )
        assert final_last_used_at is not None
        assert final_last_used_at.replace(tzinfo=UTC) == winner_now
    finally:
        release_loser.set()
        event.remove(engine, "before_cursor_execute", coordinate_updates)
        event.remove(engine, "after_cursor_execute", observe_rowcount)
        engine.dispose()


def test_unexpected_telemetry_rowcount_is_sanitized(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "未知 rowcount", None)
    original_execute = session.execute

    class UnexpectedRowcount:
        rowcount = -1

    def replace_rowcount(statement: object, *args: object, **kwargs: object) -> object:
        result = original_execute(statement, *args, **kwargs)
        if getattr(statement, "is_update", False):
            return UnexpectedRowcount()
        return result

    monkeypatch.setattr(session, "execute", replace_rowcount)

    with pytest.raises(McpTokenPersistenceFailed) as captured:
        authenticate_mcp_token(session, issued.raw_token)

    assert captured.value.stage == "telemetry"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_creation_failure_is_sanitized_rolls_back_and_session_is_reusable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    token_id = UUID("44444444-4444-4444-8444-444444444444")
    secret = "A" * 43
    raw_token = f"tickly_mcp_{token_id}.{secret}"
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def fail_commit() -> None:
        raise OperationalError(
            f"INSERT 失败 {raw_token}",
            {"token_hash": digest},
            RuntimeError("敏感数据库失败"),
        )

    with monkeypatch.context() as failure:
        failure.setattr(mcp_token_services, "uuid4", lambda: token_id)
        failure.setattr(mcp_token_services.secrets, "token_urlsafe", lambda _: secret)
        failure.setattr(session, "commit", fail_commit)
        with pytest.raises(McpTokenPersistenceFailed) as captured:
            create_mcp_token(session, user.id, "创建失败", None)

    assert captured.value.stage == "create"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert raw_token not in str(captured.value)
    assert digest not in repr(captured.value)
    assert list_mcp_tokens(session, user.id) == []
    assert create_mcp_token(session, user.id, "重试成功", None).record.id


def test_telemetry_failure_is_upstream_failure_and_session_is_reusable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "遥测失败", None)

    def fail_commit() -> None:
        raise OperationalError(
            f"UPDATE 失败 {issued.raw_token}",
            {"token_hash": issued.record.token_hash},
            RuntimeError("敏感数据库失败"),
        )

    with monkeypatch.context() as failure:
        failure.setattr(session, "commit", fail_commit)
        with pytest.raises(McpTokenPersistenceFailed) as captured:
            authenticate_mcp_token(session, issued.raw_token)

    assert not isinstance(captured.value, McpAuthenticationRequired)
    assert captured.value.stage == "telemetry"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert issued.raw_token not in str(captured.value)
    assert issued.record.token_hash not in repr(captured.value)
    assert issued.record.last_used_at is None
    assert authenticate_mcp_token(session, issued.raw_token).id == user.id


def test_revoke_is_owner_scoped_idempotent_and_missing_is_indistinguishable(
    session: Session,
) -> None:
    first = create_account(session, "first", PASSWORD)
    second = create_account(session, "second", PASSWORD)
    issued = create_mcp_token(session, first.id, "测试", None)

    errors: list[McpTokenNotFound] = []
    for user_id, token_id in (
        (second.id, issued.record.id),
        (first.id, "ffffffff-ffff-4fff-8fff-ffffffffffff"),
    ):
        with pytest.raises(McpTokenNotFound) as error:
            revoke_mcp_token(session, user_id, token_id)
        errors.append(error.value)

    assert [str(error) for error in errors] == ["", ""]
    revoke_mcp_token(session, first.id, issued.record.id)
    first_revoked_at = issued.record.revoked_at
    revoke_mcp_token(session, first.id, issued.record.id)
    assert issued.record.revoked_at == first_revoked_at
    assert session.scalar(select(McpToken).where(McpToken.id == issued.record.id))


def test_concurrent_revoke_preserves_the_first_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(tmp_path)
    winner_now = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
    loser_now = winner_now + timedelta(seconds=1)
    thread_state = local()
    both_updates_ready = Barrier(2)
    winner_committed = Event()
    loser_returned = Event()
    release_loser = Event()
    observations: list[tuple[bool, int]] = []
    observation_lock = Lock()

    with session_factory() as setup_session:
        user = create_account(setup_session, "first", PASSWORD)
        issued = create_mcp_token(setup_session, user.id, "并发撤销", None)
        user_id = user.id
        token_id = issued.record.id

    def is_revoke_update(statement: str) -> bool:
        normalized = " ".join(statement.upper().split())
        return normalized.startswith("UPDATE MCP_TOKENS SET REVOKED_AT")

    def coordinate_updates(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not is_revoke_update(statement):
            return
        both_updates_ready.wait(timeout=10)
        if not thread_state.is_winner:
            assert winner_committed.wait(timeout=10)

    def observe_rowcount(
        _connection: object,
        cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not is_revoke_update(statement):
            return
        with observation_lock:
            observations.append(
                (thread_state.is_winner, cursor.rowcount)  # type: ignore[attr-defined]
            )

    event.listen(engine, "before_cursor_execute", coordinate_updates)
    event.listen(engine, "after_cursor_execute", observe_rowcount)
    monkeypatch.setattr(
        mcp_token_services,
        "utc_now",
        lambda: thread_state.now,
    )

    def revoke_at(now: datetime, *, is_winner: bool) -> None:
        thread_state.now = now
        thread_state.is_winner = is_winner
        revoke_session = session_factory()
        try:
            revoke_mcp_token(revoke_session, user_id, token_id)
            if not is_winner:
                loser_returned.set()
                assert release_loser.wait(timeout=10)
        finally:
            if is_winner:
                winner_committed.set()
            revoke_session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            winner = executor.submit(
                revoke_at, winner_now, is_winner=True
            )
            loser = executor.submit(revoke_at, loser_now, is_winner=False)
            winner.result(timeout=15)
            assert loser_returned.wait(timeout=15)
            with session_factory() as writer_session:
                writer_session.connection().exec_driver_sql(
                    "PRAGMA busy_timeout=0"
                )
                writer = writer_session.get(User, user_id)
                assert writer is not None
                writer.timezone = "UTC"
                writer_session.commit()
            release_loser.set()
            loser.result(timeout=15)

        assert sorted(observations) == [(False, 0), (True, 1)]
        with session_factory() as verification_session:
            revoked_at = verification_session.scalar(
                select(McpToken.revoked_at).where(McpToken.id == token_id)
            )
            writer = verification_session.get(User, user_id)
            assert writer is not None
            writer.timezone = "UTC"
            verification_session.commit()
        assert revoked_at is not None
        assert revoked_at.replace(tzinfo=UTC) == winner_now
    finally:
        release_loser.set()
        event.remove(engine, "before_cursor_execute", coordinate_updates)
        event.remove(engine, "after_cursor_execute", observe_rowcount)
        engine.dispose()


def test_unexpected_revoke_rowcount_is_sanitized(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "未知 rowcount", None)
    original_execute = session.execute

    class UnexpectedRowcount:
        rowcount = -1

    def replace_rowcount(statement: object, *args: object, **kwargs: object) -> object:
        result = original_execute(statement, *args, **kwargs)
        if getattr(statement, "is_update", False):
            return UnexpectedRowcount()
        return result

    monkeypatch.setattr(session, "execute", replace_rowcount)

    with pytest.raises(McpTokenPersistenceFailed) as captured:
        revoke_mcp_token(session, user.id, issued.record.id)

    assert captured.value.stage == "revoke"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_revoke_failure_is_sanitized_rolls_back_and_session_is_reusable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "撤销失败", None)

    def fail_commit() -> None:
        raise OperationalError(
            f"UPDATE 失败 {issued.raw_token}",
            {"token_hash": issued.record.token_hash},
            RuntimeError("敏感数据库失败"),
        )

    with monkeypatch.context() as failure:
        failure.setattr(session, "commit", fail_commit)
        with pytest.raises(McpTokenPersistenceFailed) as captured:
            revoke_mcp_token(session, user.id, issued.record.id)

    assert captured.value.stage == "revoke"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert issued.raw_token not in str(captured.value)
    assert issued.record.token_hash not in repr(captured.value)
    assert issued.record.revoked_at is None
    revoke_mcp_token(session, user.id, issued.record.id)
    assert issued.record.revoked_at is not None


def test_rollback_failure_invalidates_connection_and_fresh_session_can_use_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(tmp_path)
    invalidations = 0

    with session_factory() as setup_session:
        user = create_account(setup_session, "first", PASSWORD)
        issued = create_mcp_token(setup_session, user.id, "回滚失败", None)
        raw_token = issued.raw_token
        digest = issued.record.token_hash
        user_id = user.id

    failed_session = session_factory()
    original_invalidate = failed_session.invalidate

    def fail_lookup(*_: object, **__: object) -> object:
        raise OperationalError(
            f"SELECT 失败 {raw_token}",
            {"token_hash": digest},
            RuntimeError("敏感查询失败"),
        )

    def fail_rollback() -> None:
        raise OperationalError(
            f"ROLLBACK 失败 {raw_token}",
            {"token_hash": digest},
            RuntimeError("敏感回滚失败"),
        )

    def record_invalidate() -> None:
        nonlocal invalidations
        invalidations += 1
        original_invalidate()

    try:
        with monkeypatch.context() as failure:
            failure.setattr(failed_session, "get", fail_lookup)
            failure.setattr(failed_session, "rollback", fail_rollback)
            failure.setattr(failed_session, "invalidate", record_invalidate)
            with pytest.raises(McpTokenPersistenceFailed) as captured:
                authenticate_mcp_token(failed_session, raw_token)

        assert captured.value.stage == "lookup"
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert raw_token not in str(captured.value)
        assert digest not in repr(captured.value)
        assert invalidations == 1

        with session_factory() as fresh_session:
            assert authenticate_mcp_token(fresh_session, raw_token).id == user_id
    finally:
        failed_session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "operation", ["lookup", "create", "revoke"], ids=["查询", "创建提交", "撤销写入"]
)
def test_programming_errors_are_not_misclassified_as_persistence_failures(
    session: Session,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "编程错误", None)

    def fail_programming_boundary(*_: object, **__: object) -> object:
        raise AttributeError("deliberate programmer error")

    if operation == "lookup":
        monkeypatch.setattr(session, "get", fail_programming_boundary)
        action = lambda: authenticate_mcp_token(session, issued.raw_token)
    elif operation == "create":
        monkeypatch.setattr(session, "commit", fail_programming_boundary)
        action = lambda: create_mcp_token(session, user.id, "再次创建", None)
    else:
        monkeypatch.setattr(session, "execute", fail_programming_boundary)
        action = lambda: revoke_mcp_token(session, user.id, issued.record.id)

    with pytest.raises(AttributeError, match="deliberate programmer error"):
        action()
