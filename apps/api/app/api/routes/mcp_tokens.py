"""当前 Web 用户的 MCP Token 生命周期 HTTP 契约。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.api.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.models import McpToken
from app.schemas.mcp_tokens import (
    McpTokenCreateRequest,
    McpTokenCreateResponse,
    McpTokenListResponse,
    McpTokenResponse,
)
from app.services.mcp_tokens import (
    McpTokenNotFound,
    create_mcp_token,
    list_mcp_tokens,
    mcp_token_status,
    revoke_mcp_token,
)


router = APIRouter(prefix="/mcp-tokens", tags=["mcp-tokens"])


@router.get("", response_model=McpTokenListResponse)
def list_all(session: DbSession, user: CurrentUser) -> McpTokenListResponse:
    """仅列出 access JWT 所属账号的 Token 元数据。"""

    return McpTokenListResponse(
        items=[_response(item) for item in list_mcp_tokens(session, user.id)]
    )


@router.post(
    "",
    response_model=McpTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create(
    payload: McpTokenCreateRequest,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> McpTokenCreateResponse:
    """签发当前用户的 Token；原始凭据只允许出现在本次响应。"""

    issued = create_mcp_token(
        session,
        user.id,
        payload.name,
        payload.expires_in_days,
    )
    # 一次性凭据响应不得被浏览器、代理或中间缓存持久化。
    response.headers["Cache-Control"] = "no-store"
    return McpTokenCreateResponse(
        item=_response(issued.record),
        token=issued.raw_token,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(token_id: str, session: DbSession, user: CurrentUser) -> None:
    """幂等撤销本人 Token，并隐藏其他账号 Token 是否存在。"""

    try:
        revoke_mcp_token(session, user.id, token_id)
    except McpTokenNotFound as error:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="mcp_token_not_found",
            message="MCP Token 不存在",
        ) from error


def _utc(value: datetime | None) -> datetime | None:
    """将 SQLite naive UTC 时间恢复为明确的 UTC 响应时间。"""

    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(
        tzinfo=UTC
    )


def _response(record: McpToken) -> McpTokenResponse:
    """显式白名单投影，阻止摘要和所有者字段进入 Web 响应。"""

    created_at = _utc(record.created_at)
    if created_at is None:
        raise TypeError("created_at must not be None")
    return McpTokenResponse(
        id=record.id,
        name=record.name,
        status=mcp_token_status(record),
        expires_at=_utc(record.expires_at),
        last_used_at=_utc(record.last_used_at),
        created_at=created_at,
    )
