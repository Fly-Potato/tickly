"""MCP 内部路由专用的数据库 Token 认证依赖。"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import DbSession
from app.core.errors import AppError
from app.models import User
from app.services.mcp_tokens import (
    McpAuthenticationRequired,
    authenticate_mcp_token,
)


_bearer = HTTPBearer(auto_error=False)
McpBearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(_bearer),
]


def get_mcp_current_user(
    session: DbSession,
    credentials: McpBearerCredentials,
) -> User:
    """从不透明 PAT 解析所有者，并统一隐藏所有凭据失败原因。"""

    if credentials is None:
        raise _authentication_required()
    try:
        # Token 服务负责所有者、撤销、过期和账号启用状态的原子认证边界。
        return authenticate_mcp_token(session, credentials.credentials)
    except McpAuthenticationRequired as error:
        raise _authentication_required() from error


def _authentication_required() -> AppError:
    return AppError(
        status_code=401,
        code="authentication_required",
        message="需要 MCP 认证",
        headers={"WWW-Authenticate": "Bearer"},
    )


McpCurrentUser = Annotated[User, Depends(get_mcp_current_user)]
