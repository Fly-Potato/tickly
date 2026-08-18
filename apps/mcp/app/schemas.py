"""Tickly 内部 API 响应与 MCP 工具的严格协议模型。"""

import re
from enum import StrEnum
from math import isfinite
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)


TaskPriority = Literal["low", "medium", "high"]
TaskStatusValue = Literal["new", "in_progress", "completed"]
TaskStatusFilter = Literal["all", "new", "in_progress", "completed"]
TaskSort = Literal["serial", "created_at", "due_at", "priority"]
SortOrder = Literal["asc", "desc"]


def _normalize_json_integer(value: object) -> int:
    """按 JSON Schema integer 语义校验并规范化整数输入。

    JSON 的布尔值不是数字；字符串也不得由 Pydantic 隐式转换。整值浮点在
    JSON Schema 2020-12 中属于 integer，规范化为 ``int`` 后再应用范围约束。
    """
    if isinstance(value, bool | str) or not isinstance(value, int | float):
        raise ValueError("必须是 JSON integer")
    if isinstance(value, float):
        if not isfinite(value) or not value.is_integer():
            raise ValueError("必须是 JSON integer")
        return int(value)
    return value


TaskSerial = Annotated[
    int,
    Field(ge=1, le=9_223_372_036_854_775_807),
    BeforeValidator(_normalize_json_integer),
]
TopicFilter = Annotated[str | None, Field(max_length=100)]
Cursor = Annotated[str | None, Field(min_length=1, max_length=2048)]
PageLimit = Annotated[
    int,
    Field(ge=1, le=100),
    BeforeValidator(_normalize_json_integer),
]
ParentQuery = Annotated[str | None, Field(max_length=200)]
_RFC3339_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def _require_rfc3339_datetime_string(value: object) -> object:
    """拒绝 Pydantic 支持但 MCP 契约未开放的 Unix timestamp 输入。"""
    if not isinstance(value, str) or _RFC3339_DATETIME_PATTERN.fullmatch(value) is None:
        raise ValueError("必须是 RFC3339 date-time 字符串")
    return value


McpAwareDatetime = Annotated[
    AwareDatetime,
    BeforeValidator(_require_rfc3339_datetime_string),
]


class TaskStatus(StrEnum):
    """状态写工具唯一允许的三个 Tickly 状态。"""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class CreateTaskInput(BaseModel):
    """创建工具的业务输入，拒绝状态、流水号等服务端字段。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    topic: str = Field(min_length=1, max_length=100)
    due_at: McpAwareDatetime | None = None
    parent_serial: TaskSerial | None = None


class UpdateTaskInput(BaseModel):
    """普通字段 patch；显式 null 与省略必须保留不同的上游语义。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: TaskPriority | None = None
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    due_at: McpAwareDatetime | None = None
    parent_serial: TaskSerial | None = None

    @model_validator(mode="after")
    def validate_patch_fields(self) -> Self:
        """拒绝空 patch 和非空数据库字段的显式清空。"""
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个可更新字段")
        for field_name in ("title", "description", "topic"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")
        return self


class ToolArguments(BaseModel):
    """所有工具共享的封闭根参数模型。"""

    model_config = ConfigDict(extra="forbid")


class ListTasksArguments(ToolArguments):
    status: TaskStatusFilter = "all"
    topic: TopicFilter = None
    sort: TaskSort = "created_at"
    order: SortOrder = "desc"
    cursor: Cursor = None
    limit: PageLimit = 50


class GetTaskArguments(ToolArguments):
    serial: TaskSerial


class ListTopicsArguments(ToolArguments):
    pass


class FindParentTasksArguments(ToolArguments):
    query: ParentQuery = None
    cursor: Cursor = None
    limit: PageLimit = 50


class CreateTaskArguments(ToolArguments):
    task: CreateTaskInput


class UpdateTaskArguments(ToolArguments):
    serial: TaskSerial
    patch: UpdateTaskInput


class SetTaskStatusArguments(ToolArguments):
    serial: TaskSerial
    status: TaskStatus


class ApiPayload(BaseModel):
    """拒绝上游意外字段，避免协议漂移被 MCP 静默吞掉。"""

    model_config = ConfigDict(extra="forbid")


class TaskPayload(ApiPayload):
    id: str
    serial: int
    title: str
    description: str
    priority: TaskPriority | None
    topic: str
    status: TaskStatusValue
    due_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    parent_id: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TaskGroupPayload(ApiPayload):
    task: TaskPayload
    children: list[TaskPayload]
    child_count: int
    completed_child_count: int
    context_only: bool


class TaskListPayload(ApiPayload):
    items: list[TaskGroupPayload]
    next_cursor: str | None


class TaskDetailPayload(TaskPayload):
    children: list[TaskPayload]


class TopicListPayload(ApiPayload):
    items: list[str]


class ParentOptionPayload(ApiPayload):
    id: str
    serial: int
    title: str
    topic: str
    status: TaskStatusValue


class ParentOptionPagePayload(ApiPayload):
    items: list[ParentOptionPayload]
    next_cursor: str | None


class TaskListResult(BaseModel):
    """根任务组分页结果，并保留给 Codex 展示的短摘要。"""

    summary: str
    items: list[TaskGroupPayload]
    next_cursor: str | None


class TaskDetailResult(BaseModel):
    """按流水号返回任务本体和一层直接子任务。"""

    summary: str
    task: TaskPayload
    children: list[TaskPayload]


class TopicListResult(BaseModel):
    """当前账号精确主题值与短摘要。"""

    summary: str
    items: list[str]


class ParentOptionResult(BaseModel):
    """可作为父任务的根任务候选分页结果。"""

    summary: str
    items: list[ParentOptionPayload]
    next_cursor: str | None


class TaskWriteResult(BaseModel):
    """写工具返回更新后的机器可读任务与短摘要。"""

    summary: str
    task: TaskPayload
