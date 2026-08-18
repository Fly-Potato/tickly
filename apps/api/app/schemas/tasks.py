"""Todo API 的严格请求、查询与响应契约。

写入 schema 禁止额外字段，并在长度校验前规范化自由文本。外部时间必须显式
携带时区并转换为 UTC；SQLite 读出的无时区时间按 UTC 恢复后再响应，避免把
存储层实现细节泄漏给客户端。PATCH 通过 ``model_fields_set`` 区分省略字段与
显式清空，完成时间只由 service 根据完成状态维护。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskStatusFilter(StrEnum):
    ALL = "all"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskSort(StrEnum):
    SERIAL = "serial"
    CREATED_AT = "created_at"
    DUE_AT = "due_at"
    PRIORITY = "priority"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _empty_text_to_none(value: object) -> object:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return value


def _require_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(UTC)


def _stored_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    # SQLite 会丢失时区信息；数据库读取值按既定的 UTC 存储约定恢复。
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    topic: str = Field(min_length=1, max_length=100)
    due_at: datetime | None = None
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("title", "topic", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        return _empty_text_to_none(value)

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def default_description(self) -> Self:
        # 默认值只在创建完成字段校验后生成，后续标题与描述保持彼此独立。
        if self.description is None:
            self.description = self.title
        return self


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: TaskPriority | None = None
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    status: TaskStatus | None = None
    due_at: datetime | None = None
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("title", "topic", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        return _empty_text_to_none(value)

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_patch_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个可更新字段")
        # 这些字段在数据库中非空；nullable 字段仍可靠 model_fields_set 显式清空。
        for field_name in ("title", "description", "topic", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")
        return self


class TaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatusFilter = TaskStatusFilter.ALL
    topic: str | None = Field(default=None, max_length=100)
    sort: TaskSort = TaskSort.CREATED_AT
    order: SortOrder = SortOrder.DESC
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> object:
        return _empty_text_to_none(value)


class ParentOptionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        return _empty_text_to_none(value)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    serial: int
    title: str
    description: str
    priority: TaskPriority | None
    topic: str
    status: TaskStatus
    due_at: datetime | None
    completed_at: datetime | None
    parent_id: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "due_at", "completed_at", "created_at", "updated_at", mode="before"
    )
    @classmethod
    def normalize_stored_datetime(cls, value: datetime | None) -> datetime | None:
        return _stored_utc(value)


class TaskGroupResponse(BaseModel):
    task: TaskResponse
    children: list[TaskResponse]
    child_count: int
    completed_child_count: int
    context_only: bool


class TaskListResponse(BaseModel):
    items: list[TaskGroupResponse]
    next_cursor: str | None


class TaskDetailResponse(TaskResponse):
    children: list[TaskResponse]


class TopicListResponse(BaseModel):
    items: list[str]


class ParentOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    serial: int
    title: str
    topic: str
    status: TaskStatus


class ParentOptionPageResponse(BaseModel):
    items: list[ParentOptionResponse]
    next_cursor: str | None
