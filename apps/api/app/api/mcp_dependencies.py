"""MCP 内部路由专用的认证与唯一账号依赖。"""

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import DbSession
from app.core.config import Settings
from app.core.errors import AppError
from app.models import User


class McpAuthenticationRequired(Exception):
    """MCP Token 缺失、配置不可用或校验失败。"""


class McpAccountUnavailable(Exception):
    """数据库不能唯一解析到一个启用账号。"""


def verify_mcp_token(token: str, settings: Settings) -> None:
    """只比较原始 Token 的 SHA-256 摘要，避免在配置或异常中保留凭据。"""

    expected = settings.mcp_token_sha256
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if expected is None or not secrets.compare_digest(actual, expected):
        raise McpAuthenticationRequired


def resolve_mcp_user(session: Session) -> User:
    """解析唯一启用账号；零个、多个或停用状态都按不可用失败关闭。"""

    users = list(session.scalars(select(User).limit(2)).all())
    if len(users) != 1 or not users[0].is_active:
        raise McpAccountUnavailable
    return users[0]


_bearer = HTTPBearer(auto_error=False)
McpBearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(_bearer),
]


def get_mcp_current_user(
    request: Request,
    session: DbSession,
    credentials: McpBearerCredentials,
) -> User:
    """校验 MCP 专用 Bearer Token，并把失败收敛为稳定的内部接口错误。"""

    if credentials is None:
        raise _authentication_required()
    try:
        verify_mcp_token(credentials.credentials, request.app.state.settings)
        return resolve_mcp_user(session)
    except McpAuthenticationRequired as error:
        raise _authentication_required() from error
    except McpAccountUnavailable as error:
        raise AppError(
            status_code=503,
            code="mcp_account_unavailable",
            message="MCP 账号不可用",
        ) from error


def _authentication_required() -> AppError:
    return AppError(
        status_code=401,
        code="authentication_required",
        message="需要 MCP 认证",
        headers={"WWW-Authenticate": "Bearer"},
    )


McpCurrentUser = Annotated[User, Depends(get_mcp_current_user)]
