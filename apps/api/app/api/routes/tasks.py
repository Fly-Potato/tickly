"""当前用户 Todo 的 HTTP 契约。

所有 operation 都通过 Bearer 依赖获取当前用户，并把 schema、事务和分页交给
service。路由只返回公开任务字段；不存在、跨用户与非法 ID 统一映射为稳定 404，
游标解码失败统一映射为不回显输入或底层原因的 422。
"""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.schemas.tasks import (
    TaskCreateRequest,
    TaskListQuery,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
)
from app.services.tasks import (
    InvalidCursor,
    TaskNotFound,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)


router = APIRouter(prefix="/tasks", tags=["tasks"])
TaskQuery = Annotated[TaskListQuery, Query()]


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create(
    payload: TaskCreateRequest,
    session: DbSession,
    user: CurrentUser,
) -> TaskResponse:
    task = create_task(session, user.id, payload)
    return TaskResponse.model_validate(task)


@router.get("", response_model=TaskListResponse)
def list_all(
    query: TaskQuery,
    session: DbSession,
    user: CurrentUser,
) -> TaskListResponse:
    try:
        page = list_tasks(session, user.id, query)
    except InvalidCursor as error:
        raise _invalid_cursor() from error
    return TaskListResponse(
        items=[TaskResponse.model_validate(task) for task in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{task_id}", response_model=TaskResponse)
def detail(task_id: str, session: DbSession, user: CurrentUser) -> TaskResponse:
    try:
        task = get_task(session, user.id, task_id)
    except TaskNotFound as error:
        raise _task_not_found() from error
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}", response_model=TaskResponse)
def update(
    task_id: str,
    payload: TaskUpdateRequest,
    session: DbSession,
    user: CurrentUser,
) -> TaskResponse:
    try:
        task = update_task(session, user.id, task_id, payload)
    except TaskNotFound as error:
        raise _task_not_found() from error
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(task_id: str, session: DbSession, user: CurrentUser) -> None:
    try:
        delete_task(session, user.id, task_id)
    except TaskNotFound as error:
        raise _task_not_found() from error


def _task_not_found() -> AppError:
    # 不区分 ID 格式、真实不存在或跨用户，避免通过响应枚举其他用户资源。
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="task_not_found",
        message="任务不存在",
    )


def _invalid_cursor() -> AppError:
    # 对外只暴露稳定错误，不回显 cursor 内容或底层解码失败原因。
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_cursor",
        message="分页游标无效",
    )
