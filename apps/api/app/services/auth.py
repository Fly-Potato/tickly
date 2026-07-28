"""登录、会话轮换、登出与 access token 认证服务。"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import (
    InvalidToken,
    InvalidUsername,
    decode_token,
    digest_refresh_token,
    issue_access_token,
    issue_refresh_token,
    normalize_username,
    verify_dummy_password,
    verify_password,
)
from app.models import AuthSession, User
from app.models.user import utc_now


class InvalidCredentials(Exception):
    """登录凭据错误、账号不存在或账号已停用。"""


class RefreshRequired(Exception):
    """refresh token 缺失、无效、过期或未绑定有效会话。"""


class RefreshReplayed(Exception):
    """已经被轮换消费的 refresh token 再次出现。"""


class AuthenticationRequired(Exception):
    """access token 无法解析为当前活跃用户。"""


@dataclass(frozen=True)
class AuthenticationResult:
    access_token: str
    refresh_token: str
    session: AuthSession
    expires_in: int


def login_user(
    session: Session,
    username: str,
    password: str,
    settings: Settings,
    *,
    user_agent: str | None,
) -> AuthenticationResult:
    """校验统一凭据并在一个事务中创建 refresh 会话。"""

    try:
        try:
            normalized = normalize_username(username)
        except InvalidUsername as error:
            # 非法格式也执行 dummy hash，避免格式差异成为账号枚举旁路。
            verify_dummy_password(password)
            raise InvalidCredentials from error

        user = session.scalar(select(User).where(User.username == normalized))
        if user is None:
            verify_dummy_password(password)
            raise InvalidCredentials
        if not verify_password(password, user.password_hash) or not user.is_active:
            raise InvalidCredentials

        now = utc_now()
        expires_at = now + timedelta(days=settings.refresh_token_days)
        session_id = str(uuid4())
        refresh_token = issue_refresh_token(
            user.id,
            session_id,
            settings,
            expires_at=expires_at,
        )
        auth_session = AuthSession(
            id=session_id,
            user_id=user.id,
            refresh_token_hash=digest_refresh_token(refresh_token),
            expires_at=expires_at,
            last_used_at=now,
            user_agent=user_agent[:512] if user_agent else None,
        )
        session.add(auth_session)
        access_token = issue_access_token(user.id, settings)
        session.commit()
        return AuthenticationResult(
            access_token=access_token,
            refresh_token=refresh_token,
            session=auth_session,
            expires_in=settings.access_token_minutes * 60,
        )
    except Exception:
        session.rollback()
        raise


def refresh_session(
    session: Session, refresh_token: str, settings: Settings
) -> AuthenticationResult:
    """原子消费旧摘要并轮换 refresh token，不延长会话绝对期限。

    条件更新保证两个并发请求只有一个能消费当前摘要。失败后再次读取会话；存在
    同一 ``sid`` 表示旧 token 被重放，此时先提交撤销再向调用方报告重放。
    """

    try:
        try:
            payload = decode_token(refresh_token, "refresh", settings)
        except InvalidToken as error:
            raise RefreshRequired from error

        auth_session = session.get(AuthSession, payload.sid)
        if auth_session is None or auth_session.user_id != payload.sub:
            raise RefreshRequired

        now = utc_now()
        user = session.get(User, auth_session.user_id)
        if user is None or not user.is_active:
            # refresh 必须独立复核账号状态，防止维护脚本绕过账号服务后继续轮换。
            auth_session.revoked_at = now
            session.commit()
            raise RefreshRequired

        rotated_token = issue_refresh_token(
            auth_session.user_id,
            auth_session.id,
            settings,
            expires_at=auth_session.expires_at,
        )
        rotated_digest = digest_refresh_token(rotated_token)
        old_digest = digest_refresh_token(refresh_token)
        result = session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == auth_session.id,
                AuthSession.refresh_token_hash == old_digest,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .values(refresh_token_hash=rotated_digest, last_used_at=now)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount == 0:
            existing = session.get(AuthSession, auth_session.id)
            if existing is None:
                raise RefreshRequired
            existing.revoked_at = now
            session.commit()
            raise RefreshReplayed

        access_token = issue_access_token(auth_session.user_id, settings)
        session.commit()
        return AuthenticationResult(
            access_token=access_token,
            refresh_token=rotated_token,
            session=auth_session,
            expires_in=settings.access_token_minutes * 60,
        )
    except Exception:
        session.rollback()
        raise


def logout_session(
    session: Session, refresh_token: str | None, settings: Settings
) -> None:
    """幂等撤销 refresh token 对应会话；无效输入不暴露会话是否存在。"""

    if not refresh_token:
        return
    try:
        try:
            payload = decode_token(refresh_token, "refresh", settings)
        except InvalidToken:
            return

        session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == payload.sid,
                AuthSession.user_id == payload.sub,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
            .execution_options(synchronize_session="fetch")
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


def authenticate_access_token(
    session: Session, access_token: str, settings: Settings
) -> User:
    """解码 access token 并重新读取账号状态，使停用立即生效。"""

    try:
        payload = decode_token(access_token, "access", settings)
    except InvalidToken as error:
        raise AuthenticationRequired from error

    user = session.get(User, payload.sub)
    if user is None or not user.is_active:
        raise AuthenticationRequired
    return user
