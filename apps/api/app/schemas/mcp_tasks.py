"""MCP 内部任务接口的 serial 查询与写入契约。

写入字段延续公开 Todo API 的枚举、文本和 UTC 时间规则，只把父任务引用从 UUID
替换为当前账号内的 ``parent_serial``。PATCH 依靠 ``model_fields_set`` 区分省略
与显式清空，避免 MCP 适配层推断调用方没有提供的字段。
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.tasks import TaskPriority, TaskStatus


_SQLITE_MAX_INTEGER = 2**63 - 1


def _require_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(UTC)


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


class McpTaskCreateRequest(BaseModel):
    """创建任务时只接受 MCP 工具允许写入的业务字段。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    topic: str = Field(min_length=1, max_length=100)
    due_at: datetime | None = None
    parent_serial: int | None = Field(
        default=None,
        ge=1,
        le=_SQLITE_MAX_INTEGER,
    )

    @field_validator("title", "topic", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def default_description(self) -> Self:
        # 与公开创建契约一致：空白或省略描述时以规范化后的标题初始化。
        if self.description is None:
            self.description = self.title
        return self


class McpTaskUpdateRequest(BaseModel):
    """严格表达按 serial 更新；nullable 字段可通过显式 null 清空。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: TaskPriority | None = None
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    status: TaskStatus | None = None
    due_at: datetime | None = None
    parent_serial: int | None = Field(
        default=None,
        ge=1,
        le=_SQLITE_MAX_INTEGER,
    )

    @field_validator("title", "topic", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_patch_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个可更新字段")
        # 数据库非空字段不能显式清空；其余 nullable 字段保留 null 的清空语义。
        for field_name in ("title", "description", "topic", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")
        return self
