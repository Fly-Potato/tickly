"""Todo API 的严格请求、查询与响应契约。

写入 schema 禁止额外字段，标题在长度校验前去除首尾空白，外部时间必须显式
携带时区并转换为 UTC。SQLite 读出的无时区时间按 UTC 恢复后再响应，避免把
存储层实现细节泄漏给客户端。PATCH 通过 ``model_fields_set`` 区分省略字段与
显式清空，可空字段允许 ``null``，完成时间只由 service 根据完成状态维护。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskPriority(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    COMPLETED = "completed"


class TaskSort(StrEnum):
    CREATED_AT = "created_at"
    DUE_AT = "due_at"
    PRIORITY = "priority"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


def _strip_title(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _require_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(UTC)


def _stored_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority = TaskPriority.NONE
    due_at: datetime | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        return _strip_title(value)

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    is_completed: bool | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        return _strip_title(value)

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_patch_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个可更新字段")
        for field_name in ("title", "priority", "is_completed"):
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"{field_name} 不能为 null")
        return self


class TaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus = TaskStatus.ALL
    sort: TaskSort = TaskSort.CREATED_AT
    order: SortOrder = SortOrder.DESC
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    notes: str | None
    is_completed: bool
    priority: TaskPriority
    due_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "due_at", "completed_at", "created_at", "updated_at", mode="before"
    )
    @classmethod
    def normalize_stored_datetime(cls, value: datetime | None) -> datetime | None:
        return _stored_utc(value)


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    next_cursor: str | None
