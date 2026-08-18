"""MCP 服务专用的内部只读任务 HTTP 契约。

路由只信任 MCP Bearer 依赖解析出的唯一账号，并继续把账号 ID 交给现有
任务 service 约束所有权。固定集合路由先于流水号详情注册；整个 router 不进入
公开 OpenAPI，避免 Web 契约与服务间契约混合。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.dependencies import DbSession
from app.api.mcp_dependencies import McpCurrentUser
from app.core.errors import AppError
from app.schemas.mcp_tasks import McpParentOptionQuery
from app.schemas.tasks import (
    ParentOptionPageResponse,
    ParentOptionResponse,
    TaskDetailResponse,
    TaskGroupResponse,
    TaskListQuery,
    TaskListResponse,
    TaskResponse,
    TopicListResponse,
)
from app.services.tasks import (
    InvalidCursor,
    TaskNotFound,
    get_task_detail_by_serial,
    list_parent_options,
    list_tasks,
    list_topics,
)


router = APIRouter(prefix="/internal/mcp/v1/tasks", include_in_schema=False)
TaskQuery = Annotated[TaskListQuery, Query()]
ParentQuery = Annotated[McpParentOptionQuery, Query()]
TaskSerial = Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)]


@router.get("", response_model=TaskListResponse)
def list_all(
    query: TaskQuery,
    session: DbSession,
    user: McpCurrentUser,
) -> TaskListResponse:
    """按唯一账号返回完整根任务组，并保留现有稳定 cursor。"""

    try:
        page = list_tasks(session, user.id, query)
    except InvalidCursor as error:
        raise _invalid_cursor() from error
    return TaskListResponse(
        items=[
            TaskGroupResponse(
                task=TaskResponse.model_validate(group.task),
                children=[
                    TaskResponse.model_validate(child) for child in group.children
                ],
                child_count=group.child_count,
                completed_child_count=group.completed_child_count,
                context_only=group.context_only,
            )
            for group in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/topics", response_model=TopicListResponse)
def topics(session: DbSession, user: McpCurrentUser) -> TopicListResponse:
    return TopicListResponse(items=list_topics(session, user.id))


@router.get("/parent-options", response_model=ParentOptionPageResponse)
def parent_options(
    query: ParentQuery,
    session: DbSession,
    user: McpCurrentUser,
) -> ParentOptionPageResponse:
    try:
        page = list_parent_options(session, user.id, query)
    except InvalidCursor as error:
        raise _invalid_cursor() from error
    return ParentOptionPageResponse(
        items=[ParentOptionResponse.model_validate(task) for task in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{serial}", response_model=TaskDetailResponse)
def detail(
    serial: TaskSerial,
    session: DbSession,
    user: McpCurrentUser,
) -> TaskDetailResponse:
    """按账号内流水号读取任务，不暴露跨账号或不存在的区别。"""

    try:
        result = get_task_detail_by_serial(session, user.id, serial)
    except TaskNotFound as error:
        raise _task_not_found() from error
    return TaskDetailResponse(
        **TaskResponse.model_validate(result.task).model_dump(),
        children=[TaskResponse.model_validate(child) for child in result.children],
    )


def _task_not_found() -> AppError:
    # serial 不存在和属于其他账号必须共享响应，避免枚举其他账号资源。
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="task_not_found",
        message="任务不存在",
    )


def _invalid_cursor() -> AppError:
    # 不回显 cursor 或底层解析异常，保持现有稳定错误边界。
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_cursor",
        message="分页游标无效",
    )
