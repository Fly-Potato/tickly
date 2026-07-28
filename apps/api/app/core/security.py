"""认证边界使用的纯安全原语。

本模块只负责规范化、密码散列、JWT 编解码和 refresh token 摘要，不访问
数据库，也不记录任何凭据。调用方只会收到稳定的领域异常，避免第三方库异常
携带 token 或散列细节穿透到 HTTP 与 CLI 边界。
"""

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import jwt
from jwt.exceptions import InvalidTokenError as PyJWTInvalidTokenError
from pydantic import BaseModel, ValidationError
from pwdlib import PasswordHash

from app.core.config import Settings


class InvalidUsername(ValueError):
    """用户名不符合规范化规则。"""


class InvalidPassword(ValueError):
    """密码不符合最小安全边界。"""


class InvalidToken(ValueError):
    """JWT 未通过固定算法、claims 或类型校验。"""


class TokenPayload(BaseModel):
    """通过签名、受众和用途校验后的 JWT 载荷。"""

    sub: str
    jti: str
    type: Literal["access", "refresh"]
    iss: str
    aud: str | list[str]
    iat: datetime
    exp: datetime
    sid: str | None = None


_USERNAME_PATTERN = re.compile(r"[a-z0-9_-]{3,32}")
_PASSWORD_HASH = PasswordHash.recommended()
# 未知用户仍执行一次同等成本的 Argon2 校验，缩小账号枚举的时间差异。
_DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash("tickly-dummy-password-value")


def normalize_username(value: str) -> str:
    """清理并校验登录用户名，数据库中只持久化同一种规范形式。"""

    normalized = value.strip().lower()
    if _USERNAME_PATTERN.fullmatch(normalized) is None:
        raise InvalidUsername("用户名必须为 3 至 32 位小写字母、数字、下划线或连字符")
    return normalized


def validate_password(value: str) -> str:
    """执行密码进入散列前的最小长度校验，不改变用户输入。"""

    if len(value) < 12:
        raise InvalidPassword("密码至少需要 12 个字符")
    return value


def hash_password(value: str) -> str:
    """使用当前推荐的 Argon2 参数散列合规密码。"""

    return _PASSWORD_HASH.hash(validate_password(value))


def verify_password(value: str, encoded: str) -> bool:
    """验证密码；损坏或不受支持的散列统一视为不匹配。"""

    try:
        return _PASSWORD_HASH.verify(value, encoded)
    except (TypeError, ValueError):
        return False


def verify_dummy_password(value: str) -> None:
    """为不存在的账号执行固定散列校验，不向调用方暴露验证结果。"""

    _PASSWORD_HASH.verify(value, _DUMMY_PASSWORD_HASH)


def issue_access_token(user_id: str, settings: Settings) -> str:
    """签发短期 access token；会话状态不写入该 token。"""

    now = datetime.now(UTC)
    return _encode_token(
        {
            "sub": user_id,
            "jti": str(uuid4()),
            "type": "access",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
        },
        settings,
    )


def issue_refresh_token(
    user_id: str,
    session_id: str,
    settings: Settings,
    *,
    expires_at: datetime | None = None,
) -> str:
    """签发绑定会话的 refresh token，并允许轮换时复用绝对到期时间。"""

    now = datetime.now(UTC)
    return _encode_token(
        {
            "sub": user_id,
            "sid": session_id,
            "jti": str(uuid4()),
            "type": "refresh",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": expires_at
            if expires_at is not None
            else now + timedelta(days=settings.refresh_token_days),
        },
        settings,
    )


def decode_token(
    token: str,
    expected_type: Literal["access", "refresh"],
    settings: Settings,
) -> TokenPayload:
    """验证固定算法、标准 claims 与 token 用途，返回类型化载荷。"""

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["sub", "jti", "type", "iss", "aud", "iat", "exp"]
            },
        )
        payload = TokenPayload.model_validate(claims)
    except (PyJWTInvalidTokenError, ValidationError, TypeError, ValueError) as error:
        raise InvalidToken("JWT 无效或已过期") from error

    if payload.type != expected_type:
        raise InvalidToken("JWT 用途不匹配")
    if expected_type == "refresh" and not payload.sid:
        raise InvalidToken("refresh token 缺少会话标识")
    return payload


def digest_refresh_token(token: str) -> str:
    """生成可持久化的 refresh token SHA-256 摘要，数据库不保存原文。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_digest_matches(token: str, digest: str) -> bool:
    """以恒定时间比较 refresh token 与持久化摘要。"""

    return hmac.compare_digest(digest_refresh_token(token), digest)


def _encode_token(claims: dict[str, object], settings: Settings) -> str:
    """集中固定签名算法，避免不同签发路径产生算法漂移。"""

    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
