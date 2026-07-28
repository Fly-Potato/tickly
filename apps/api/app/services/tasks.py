"""当前用户任务的业务用例、事务和稳定分页。

调用方传入已验证的 schema 与当前用户 ID；所有单任务读取都在同一条 SQL 中
同时约束任务 ID 和用户 ID，因此格式错误、不存在与跨用户访问使用同一异常。
创建、更新和硬删除各自拥有提交边界，失败时回滚 Session 中全部挂起改动；
读取和列表没有外部副作用。完成时间只在首次完成时写入，取消完成时清空。

SQLite 可能返回无时区时间，分页游标统一按 UTC 解释。游标是严格校验的
Base64URL JSON，不是授权凭据或秘密；它绑定状态、排序和方向，并用排序值加
任务 ID 做 keyset 定位。列表始终按用户过滤，不返回 total，也不记录任务内容。
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import and_, asc, case, desc, or_, select
from sqlalchemy.orm import Session

from app.models import Task
from app.models.user import utc_now
from app.schemas.tasks import (
    SortOrder,
    TaskCreateRequest,
    TaskListQuery,
    TaskSort,
    TaskStatus,
    TaskUpdateRequest,
)


_PRIORITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


class TaskNotFound(Exception):
    """任务不存在或不属于当前用户。"""


class InvalidCursor(Exception):
    """分页 cursor 损坏、过期版本或不匹配当前查询。"""


@dataclass(frozen=True)
class TaskPage:
    items: list[Task]
    next_cursor: str | None


class _CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    status: TaskStatus
    sort: TaskSort
    order: SortOrder
    null_bucket: bool
    value: str | int | None
    id: UUID


@dataclass(frozen=True)
class _CursorPosition:
    null_bucket: bool
    value: datetime | int | None
    task_id: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _database_time(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _iso_utc(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_cursor_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidCursor
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidCursor from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidCursor
    return _database_time(parsed)


def _decode_cursor(cursor: str, query: TaskListQuery) -> _CursorPosition:
    """严格解码游标，并绑定创建它的筛选与排序条件。"""

    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            (cursor + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = _CursorPayload.model_validate_json(raw)
    except (
        UnicodeError,
        ValueError,
        TypeError,
        binascii.Error,
        JSONDecodeError,
        ValidationError,
    ) as error:
        raise InvalidCursor from error

    if (
        payload.status is not query.status
        or payload.sort is not query.sort
        or payload.order is not query.order
    ):
        raise InvalidCursor

    if query.sort is TaskSort.PRIORITY:
        if (
            payload.null_bucket
            or type(payload.value) is not int
            or not 0 <= payload.value <= 3
        ):
            raise InvalidCursor
        value: datetime | int | None = payload.value
    elif query.sort is TaskSort.DUE_AT and payload.null_bucket:
        if payload.value is not None:
            raise InvalidCursor
        value = None
    else:
        if payload.null_bucket:
            raise InvalidCursor
        value = _parse_cursor_time(payload.value)

    return _CursorPosition(
        null_bucket=payload.null_bucket,
        value=value,
        task_id=str(payload.id),
    )


def _encode_cursor(task: Task, query: TaskListQuery) -> str:
    if query.sort is TaskSort.PRIORITY:
        null_bucket = False
        value: str | int | None = _PRIORITY_RANK[task.priority]
    elif query.sort is TaskSort.DUE_AT:
        null_bucket = task.due_at is None
        value = None if task.due_at is None else _iso_utc(task.due_at)
    else:
        null_bucket = False
        value = _iso_utc(task.created_at)

    raw = _CursorPayload(
        v=1,
        status=query.status,
        sort=query.sort,
        order=query.order,
        null_bucket=null_bucket,
        value=value,
        id=UUID(task.id),
    ).model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def create_task(session: Session, user_id: str, payload: TaskCreateRequest) -> Task:
    """创建当前用户的未完成任务并提交。"""

    now = utc_now()
    task = Task(
        user_id=user_id,
        title=payload.title,
        notes=payload.notes,
        priority=payload.priority.value,
        due_at=payload.due_at,
        is_completed=False,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    try:
        session.add(task)
        session.commit()
        session.refresh(task)
        return task
    except Exception:
        # service 自己拥有提交边界，失败时必须清除调用方此前挂起的全部改动。
        session.rollback()
        raise


def get_task(session: Session, user_id: str, task_id: str) -> Task:
    """用同一 SQL 条件按任务 ID 和当前用户读取。"""

    task = session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise TaskNotFound
    return task


def update_task(
    session: Session,
    user_id: str,
    task_id: str,
    payload: TaskUpdateRequest,
) -> Task:
    """部分更新任务，在同一事务维护完成时间和更新时间。"""

    try:
        task = get_task(session, user_id, task_id)
        fields = payload.model_fields_set

        if "title" in fields:
            task.title = payload.title  # type: ignore[assignment]
        if "notes" in fields:
            task.notes = payload.notes
        if "priority" in fields:
            task.priority = payload.priority.value  # type: ignore[union-attr]
        if "due_at" in fields:
            task.due_at = payload.due_at

        now = utc_now()
        if "is_completed" in fields:
            # 只有从未完成切换为完成才写入时间，重复完成保持首次完成时间稳定。
            if payload.is_completed:
                if not task.is_completed:
                    task.completed_at = now
                task.is_completed = True
            else:
                task.is_completed = False
                task.completed_at = None

        task.updated_at = now
        session.commit()
        session.refresh(task)
        return task
    except Exception:
        # PATCH 是单一事务，任何挂起写入失败都不能留下部分字段变更。
        session.rollback()
        raise


def _after_value(
    sort_expression,
    value: datetime | int,
    task_id: str,
    order: SortOrder,
):
    if order is SortOrder.ASC:
        return or_(
            sort_expression > value,
            and_(sort_expression == value, Task.id > task_id),
        )
    return or_(
        sort_expression < value,
        and_(sort_expression == value, Task.id < task_id),
    )


def list_tasks(
    session: Session,
    user_id: str,
    query: TaskListQuery,
) -> TaskPage:
    """按用户、筛选和稳定 keyset cursor 返回一页任务。"""

    statement = select(Task).where(Task.user_id == user_id)
    if query.status is TaskStatus.ACTIVE:
        statement = statement.where(Task.is_completed.is_(False))
    elif query.status is TaskStatus.COMPLETED:
        statement = statement.where(Task.is_completed.is_(True))

    priority_expression = case(_PRIORITY_RANK, value=Task.priority, else_=0)
    direction = asc if query.order is SortOrder.ASC else desc
    if query.sort is TaskSort.DUE_AT:
        sort_expression = Task.due_at
        # 截止时间为空的任务在升降序中都固定放在末尾，避免数据库默认 NULL 顺序漂移。
        statement = statement.order_by(
            asc(Task.due_at.is_(None)),
            direction(Task.due_at),
            direction(Task.id),
        )
    elif query.sort is TaskSort.PRIORITY:
        sort_expression = priority_expression
        statement = statement.order_by(
            direction(priority_expression),
            direction(Task.id),
        )
    else:
        sort_expression = Task.created_at
        statement = statement.order_by(
            direction(Task.created_at),
            direction(Task.id),
        )

    if query.cursor is not None:
        cursor = _decode_cursor(query.cursor, query)
        if query.sort is TaskSort.DUE_AT and cursor.null_bucket:
            id_predicate = (
                Task.id > cursor.task_id
                if query.order is SortOrder.ASC
                else Task.id < cursor.task_id
            )
            statement = statement.where(Task.due_at.is_(None), id_predicate)
        elif query.sort is TaskSort.DUE_AT:
            if not isinstance(cursor.value, datetime):
                raise InvalidCursor
            statement = statement.where(
                or_(
                    Task.due_at.is_(None),
                    and_(
                        Task.due_at.is_not(None),
                        _after_value(
                            Task.due_at,
                            cursor.value,
                            cursor.task_id,
                            query.order,
                        ),
                    ),
                )
            )
        else:
            if cursor.value is None:
                raise InvalidCursor
            statement = statement.where(
                _after_value(
                    sort_expression,
                    cursor.value,
                    cursor.task_id,
                    query.order,
                )
            )

    # 多取一条仅用于判断是否还有下一页，不返回 total 或执行额外 count 查询。
    rows = list(session.scalars(statement.limit(query.limit + 1)).all())
    has_more = len(rows) > query.limit
    items = rows[: query.limit]
    next_cursor = _encode_cursor(items[-1], query) if has_more else None
    return TaskPage(items=items, next_cursor=next_cursor)


def delete_task(session: Session, user_id: str, task_id: str) -> None:
    """硬删除当前用户任务；跨用户与不存在使用相同异常。"""

    try:
        task = get_task(session, user_id, task_id)
        session.delete(task)
        session.commit()
    except Exception:
        # 查询、删除或提交任一环节失败都恢复到调用前的持久化状态。
        session.rollback()
        raise
