"""Web MCP Token 管理接口的公开请求与响应 schema。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


McpTokenStatus = Literal["active", "expired", "revoked"]


def _strict_expiry_days(value: object) -> object:
    """保留 Literal 枚举文档，同时拒绝 bool、浮点和字符串的隐式转换。"""

    if type(value) is not int:
        raise ValueError("有效期必须是整数天数")
    return value


McpTokenExpiryDays = Annotated[
    Literal[90, 365],
    BeforeValidator(_strict_expiry_days),
]


class McpTokenCreateRequest(BaseModel):
    """仅接收显示名称和固定有效期，不允许客户端指定所有者。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    expires_in_days: McpTokenExpiryDays | None = 365

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if "\0" in normalized:
            raise ValueError("Token 名称不能包含 NUL 字符")
        return normalized


class McpTokenResponse(BaseModel):
    id: str
    name: str
    status: McpTokenStatus
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class McpTokenListResponse(BaseModel):
    items: list[McpTokenResponse]


class McpTokenCreateResponse(BaseModel):
    item: McpTokenResponse
    token: str
