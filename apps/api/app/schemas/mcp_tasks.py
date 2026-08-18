"""MCP 内部任务接口的只读查询契约。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpParentOptionQuery(BaseModel):
    """内部父候选查询保持与公开接口一致的分页和文本规范化语义。"""

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value
