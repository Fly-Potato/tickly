"""用户级 MCP Token 的签发、鉴权与生命周期事务边界。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import secrets
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.models import McpToken, User
from app.models.user import utc_now


TOKEN_PREFIX = "tickly_mcp_"
_SECRET_BYTES = 32
_SECRET_LENGTH = 43
_TOKEN_LENGTH = len(TOKEN_PREFIX) + 36 + 1 + _SECRET_LENGTH
_TOKEN_PATTERN = re.compile(
    rf"^{TOKEN_PREFIX}"
    r"(?P<token_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})\."
    rf"(?P<secret>[A-Za-z0-9_-]{{{_SECRET_LENGTH}}})$"
)
_LAST_USED_INTERVAL = timedelta(hours=1)
_DUMMY_DIGEST = "0" * 64
_EXPIRY_CHOICES = (90, 365, None)

McpTokenStatus = Literal["active", "expired", "revoked"]
McpTokenPersistenceStage = Literal["create", "lookup", "telemetry", "revoke"]
McpTokenValidationReason = Literal["type", "empty", "too_long", "nul"]


class McpAuthenticationRequired(Exception):
    """MCP Token 无法解析为当前活跃用户。"""


class McpTokenNotFound(Exception):
    """当前用户不存在指定 Token。"""


class McpTokenPersistenceFailed(Exception):
    """Token 持久化边界失败，且不携带底层异常或凭据内容。"""

    def __init__(self, stage: McpTokenPersistenceStage) -> None:
        super().__init__(stage)
        self.stage = stage


class McpTokenValidationError(ValueError):
    """Token 输入违反服务层不变量，且不回显调用方原值。"""

    def __init__(self, field: str, reason: McpTokenValidationReason) -> None:
        super().__init__(field, reason)
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class IssuedMcpToken:
    """只在签发结果中携带一次原始 Token，并从 repr 中排除它。"""

    record: McpToken
    raw_token: str = field(repr=False)


def create_mcp_token(
    session: Session,
    user_id: str,
    name: str,
    expires_in_days: int | None,
) -> IssuedMcpToken:
    """生成高熵 Token 并只持久化完整 Token 的 SHA-256 摘要。

    有效期是服务层不变量，防止内部调用绕过 HTTP schema。原始 Token 只存在于
    返回值，不进入 ORM 字段或底层异常。数据库失败会回滚，并转换成可与凭据
    错误区分的脱敏异常。
    """

    if type(expires_in_days) is not int and expires_in_days is not None:
        raise ValueError("expires_in_days must be 90, 365, or None")
    if expires_in_days not in _EXPIRY_CHOICES:
        raise ValueError("expires_in_days must be 90, 365, or None")

    normalized_name = _normalize_name(name)
    now = utc_now()
    token_id = str(uuid4())
    raw_token = (
        f"{TOKEN_PREFIX}{token_id}.{secrets.token_urlsafe(_SECRET_BYTES)}"
    )
    record = McpToken(
        id=token_id,
        user_id=user_id,
        name=normalized_name,
        token_hash=_digest(raw_token),
        expires_at=(
            now + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        ),
        created_at=now,
    )

    failed = False
    try:
        session.add(record)
        session.commit()
    except SQLAlchemyError:
        _rollback_safely(session)
        failed = True
    if failed:
        # 离开 except 后创建异常，避免底层异常通过 context 泄漏 SQL 参数。
        raise McpTokenPersistenceFailed("create")
    return IssuedMcpToken(record=record, raw_token=raw_token)


def list_mcp_tokens(session: Session, user_id: str) -> list[McpToken]:
    """只返回指定所有者的 Token，并使用稳定的倒序分页前置顺序。"""

    return list(
        session.scalars(
            select(McpToken)
            .where(McpToken.user_id == user_id)
            .order_by(McpToken.created_at.desc(), McpToken.id.desc())
        ).all()
    )


def authenticate_mcp_token(session: Session, raw_token: str) -> User:
    """从 Token 自身定位记录并解析活跃所有者。

    先执行严格定长解析，再决定是否散列和查询，避免超大攻击输入造成无界散列
    或数据库工作。格式正确但不存在的 locator 仍执行摘要比较；格式错误的输入
    使用固定 dummy 摘要比较。所有凭据失败共享同一无信息异常，持久化失败则
    保持为独立的上游失败。
    """

    token_id = _token_id(raw_token)
    record: McpToken | None = None
    actual_digest = _DUMMY_DIGEST
    if token_id is not None:
        lookup_failed = False
        try:
            record = session.get(McpToken, token_id)
        except SQLAlchemyError:
            _rollback_safely(session)
            lookup_failed = True
        if lookup_failed:
            raise McpTokenPersistenceFailed("lookup")
        actual_digest = _digest(raw_token)

    expected_digest = record.token_hash if record is not None else _DUMMY_DIGEST
    digest_matches = hmac.compare_digest(actual_digest, expected_digest)
    now = _as_utc(utc_now())
    if (
        token_id is None
        or record is None
        or not digest_matches
        or record.revoked_at is not None
        or _is_expired(record, now)
    ):
        raise McpAuthenticationRequired

    owner_lookup_failed = False
    user: User | None = None
    try:
        user = session.get(User, record.user_id)
    except SQLAlchemyError:
        _rollback_safely(session)
        owner_lookup_failed = True
    if owner_lookup_failed:
        raise McpTokenPersistenceFailed("lookup")
    if user is None or not user.is_active:
        raise McpAuthenticationRequired

    last_used_at = (
        _as_utc(record.last_used_at)
        if record.last_used_at is not None
        else None
    )
    if last_used_at is not None and last_used_at > now - _LAST_USED_INTERVAL:
        set_committed_value(record, "last_used_at", last_used_at)
        return user

    telemetry_update_failed = False
    try:
        telemetry_update = session.execute(
            update(McpToken)
            .where(
                McpToken.id == record.id,
                or_(
                    McpToken.last_used_at.is_(None),
                    McpToken.last_used_at
                    <= now - _LAST_USED_INTERVAL,
                ),
            )
            .values(last_used_at=now)
            .execution_options(synchronize_session=False)
        )
    except SQLAlchemyError:
        _rollback_safely(session)
        telemetry_update_failed = True
    if telemetry_update_failed:
        raise McpTokenPersistenceFailed("telemetry")

    rowcount = telemetry_update.rowcount
    if rowcount not in (0, 1):
        _rollback_safely(session)
        raise McpTokenPersistenceFailed("telemetry")

    telemetry_commit_failed = False
    try:
        # UPDATE 即使未命中也可能持有 SQLite writer transaction，必须先结束事务。
        session.commit()
    except SQLAlchemyError:
        _rollback_safely(session)
        telemetry_commit_failed = True
    if telemetry_commit_failed:
        raise McpTokenPersistenceFailed("telemetry")

    if rowcount == 1:
        set_committed_value(record, "last_used_at", now)
        return user

    telemetry_refresh_failed = False
    try:
        # 并发胜者已更新数据库；事务结束后刷新只修正当前身份映射。
        session.refresh(record, ["last_used_at"])
    except SQLAlchemyError:
        _rollback_safely(session)
        telemetry_refresh_failed = True
    if telemetry_refresh_failed:
        raise McpTokenPersistenceFailed("telemetry")
    if record.last_used_at is not None:
        set_committed_value(
            record,
            "last_used_at",
            _as_utc(record.last_used_at),
        )
    return user


def revoke_mcp_token(session: Session, user_id: str, token_id: str) -> None:
    """通过不可逆条件 UPDATE 原子撤销，并保持 owner-scoped 幂等语义。"""

    now = utc_now()
    update_failed = False
    try:
        revoke_update = session.execute(
            update(McpToken)
            .where(
                McpToken.id == token_id,
                McpToken.user_id == user_id,
                McpToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
    except SQLAlchemyError:
        _rollback_safely(session)
        update_failed = True
    if update_failed:
        raise McpTokenPersistenceFailed("revoke")

    rowcount = revoke_update.rowcount
    if rowcount not in (0, 1):
        _rollback_safely(session)
        raise McpTokenPersistenceFailed("revoke")

    commit_failed = False
    try:
        session.commit()
    except SQLAlchemyError:
        _rollback_safely(session)
        commit_failed = True
    if commit_failed:
        raise McpTokenPersistenceFailed("revoke")

    if rowcount == 1:
        identity_key = session.identity_key(McpToken, token_id)
        loaded_record = session.identity_map.get(identity_key)
        if isinstance(loaded_record, McpToken):
            set_committed_value(loaded_record, "revoked_at", now)
        return

    lookup_failed = False
    record: McpToken | None = None
    try:
        record = session.scalar(
            select(McpToken)
            .where(
                McpToken.id == token_id,
                McpToken.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
    except SQLAlchemyError:
        _rollback_safely(session)
        lookup_failed = True
    if lookup_failed:
        raise McpTokenPersistenceFailed("revoke")
    if record is None:
        raise McpTokenNotFound
    if record.revoked_at is None:
        _rollback_safely(session)
        raise McpTokenPersistenceFailed("revoke")
    set_committed_value(record, "revoked_at", _as_utc(record.revoked_at))


def mcp_token_status(
    record: McpToken, now: datetime | None = None
) -> McpTokenStatus:
    """返回展示状态；撤销状态优先于过期状态。"""

    current = _as_utc(now or utc_now())
    if record.revoked_at is not None:
        return "revoked"
    if _is_expired(record, current):
        return "expired"
    return "active"


def _token_id(raw_token: str) -> str | None:
    """在任何散列或数据库访问前执行固定上限的规范格式解析。"""

    if not isinstance(raw_token, str) or len(raw_token) != _TOKEN_LENGTH:
        return None
    match = _TOKEN_PATTERN.fullmatch(raw_token)
    if match is None:
        return None
    candidate = match.group("token_id")
    try:
        parsed = UUID(candidate)
    except ValueError:
        return None
    return candidate if str(parsed) == candidate else None


def _normalize_name(name: str) -> str:
    """在产生任何凭据材料前规范化并验证显示名称。"""

    if not isinstance(name, str):
        raise McpTokenValidationError("name", "type")
    normalized = name.strip()
    if not normalized:
        raise McpTokenValidationError("name", "empty")
    if len(normalized) > 64:
        raise McpTokenValidationError("name", "too_long")
    if "\0" in normalized:
        raise McpTokenValidationError("name", "nul")
    return normalized


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime:
    """将 SQLite 读出的 naive 时间按其原始 UTC 语义恢复。"""

    if value is None:
        raise TypeError("datetime value is required")
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(
        tzinfo=UTC
    )


def _is_expired(record: McpToken, now: datetime) -> bool:
    expires_at = record.expires_at
    return expires_at is not None and _as_utc(expires_at) <= _as_utc(now)


def _rollback_safely(session: Session) -> None:
    """回滚失败时废弃连接，阻止不可信连接回到连接池。"""

    try:
        session.rollback()
    except SQLAlchemyError:
        try:
            session.invalidate()
        except SQLAlchemyError:
            pass
