"""Tickly 内部 API 响应与 MCP 只读工具的严格协议模型。"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


TaskPriority = Literal["low", "medium", "high"]
TaskStatusValue = Literal["new", "in_progress", "completed"]
TaskStatusFilter = Literal["all", "new", "in_progress", "completed"]
TaskSort = Literal["serial", "created_at", "due_at", "priority"]
SortOrder = Literal["asc", "desc"]
TaskSerial = Annotated[
    int,
    Field(strict=True, ge=1, le=9_223_372_036_854_775_807),
]
TopicFilter = Annotated[str | None, Field(max_length=100)]
Cursor = Annotated[str | None, Field(min_length=1, max_length=2048)]
PageLimit = Annotated[int, Field(strict=True, ge=1, le=100)]
ParentQuery = Annotated[str | None, Field(max_length=200)]


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
