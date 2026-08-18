"""Tickly 内部 API 响应在 MCP 边界上的严格镜像。"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict


TaskPriority = Literal["low", "medium", "high"]
TaskStatusValue = Literal["new", "in_progress", "completed"]


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
