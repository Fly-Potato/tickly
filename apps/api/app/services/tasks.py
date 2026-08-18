"""当前用户任务的业务用例、事务和稳定分页。

调用方传入已验证的 schema 与当前用户 ID；所有单任务读取都在同一条 SQL 中
同时约束任务 ID 和用户 ID，因此格式错误、不存在与跨用户访问使用同一异常。
创建、更新和硬删除各自拥有提交边界，失败时回滚 Session 中全部挂起改动；
读取和列表没有外部副作用。每次从非 completed 进入 completed 时写入完成
时间，重复 completed 保留原值，离开 completed 时清空。

SQLite 可能返回无时区时间，分页游标统一按 UTC 解释。游标是严格校验的
Base64URL JSON，不是授权凭据或秘密；它绑定状态、排序和方向，并用排序值加
任务 ID 做 keyset 定位。列表按根任务分页，再用一次批量查询装配直接子任务；
始终按用户过滤，不返回 total，也不记录任务内容。
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import and_, asc, case, desc, exists, false, or_, select, true, update
from sqlalchemy.orm import Session, aliased

from app.models import Task, User
from app.models.user import utc_now
from app.schemas.tasks import (
    ParentOptionQuery,
    SortOrder,
    TaskCreateRequest,
    TaskListQuery,
    TaskSort,
    TaskStatus,
    TaskStatusFilter,
    TaskUpdateRequest,
)


_PRIORITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3}
_SQLITE_MAX_INTEGER = 2**63 - 1


class TaskNotFound(Exception):
    """任务不存在或不属于当前用户。"""


class InvalidCursor(Exception):
    """分页 cursor 损坏、过期版本或不匹配当前查询。"""


class InvalidTaskRelationship(Exception):
    """父待办不存在、跨用户或违反一层父子关系。"""


@dataclass(frozen=True)
class TaskGroup:
    task: Task
    children: list[Task]
    child_count: int
    completed_child_count: int
    context_only: bool


@dataclass(frozen=True)
class TaskPage:
    items: list[TaskGroup]
    next_cursor: str | None


@dataclass(frozen=True)
class ParentOptionPage:
    items: list[Task]
    next_cursor: str | None


@dataclass(frozen=True)
class TaskDetail:
    task: Task
    children: list[Task]


class _CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    status: TaskStatusFilter
    topic: str | None
    sort: TaskSort
    order: SortOrder
    null_bucket: bool
    value: str | int | None
    id: UUID

    @field_validator("v", mode="before")
    @classmethod
    def validate_version_exact_type(cls, value: object) -> object:
        """版本字段必须是 JSON 整数 1，不能让 bool 以整数子类身份通过。"""

        if type(value) is not int or value != 1:
            raise ValueError("cursor 版本必须是整数 1")
        return value


@dataclass(frozen=True)
class _CursorPosition:
    null_bucket: bool
    value: datetime | int | None
    task_id: str


class _ParentCursorPayload(BaseModel):
    """父候选专用游标，字段集合不得与完整任务列表游标混用。"""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    query: str | None
    serial: int
    id: UUID

    @field_validator("v", mode="before")
    @classmethod
    def validate_version_exact_type(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("父候选 cursor 版本必须是整数 1")
        return value

    @field_validator("serial", mode="before")
    @classmethod
    def validate_serial_exact_type_and_range(cls, value: object) -> object:
        """先拒绝 bool 和越界值，避免 SQLite 绑定参数时泄漏溢出异常。"""

        if (
            type(value) is not int
            or not 1 <= value <= _SQLITE_MAX_INTEGER
        ):
            raise ValueError("父候选 cursor serial 必须是 SQLite 正整数")
        return value


@dataclass(frozen=True)
class _ParentCursorPosition:
    serial: int
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
        payload = _CursorPayload.model_validate_json(raw, strict=True)
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
        or payload.topic != query.topic
        or payload.sort is not query.sort
        or payload.order is not query.order
    ):
        raise InvalidCursor

    if query.sort is TaskSort.PRIORITY:
        if type(payload.value) is not int:
            raise InvalidCursor
        if payload.null_bucket:
            if payload.value != _PRIORITY_RANK[None]:
                raise InvalidCursor
        elif not 1 <= payload.value <= 3:
            raise InvalidCursor
        value: datetime | int | None = payload.value
    elif query.sort is TaskSort.SERIAL:
        if (
            payload.null_bucket
            or type(payload.value) is not int
            or not 1 <= payload.value <= _SQLITE_MAX_INTEGER
        ):
            raise InvalidCursor
        value = payload.value
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
        null_bucket = task.priority is None
        value: str | int | None = _PRIORITY_RANK[task.priority]
    elif query.sort is TaskSort.DUE_AT:
        null_bucket = task.due_at is None
        value = None if task.due_at is None else _iso_utc(task.due_at)
    elif query.sort is TaskSort.SERIAL:
        null_bucket = False
        value = task.serial
    else:
        null_bucket = False
        value = _iso_utc(task.created_at)

    raw = _CursorPayload(
        v=1,
        status=query.status,
        topic=query.topic,
        sort=query.sort,
        order=query.order,
        null_bucket=null_bucket,
        value=value,
        id=UUID(task.id),
    ).model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_parent_cursor(
    cursor: str,
    query: ParentOptionQuery,
) -> _ParentCursorPosition:
    """严格解码父候选游标，并绑定 schema 已规范化的搜索词。"""

    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            (cursor + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = _ParentCursorPayload.model_validate_json(raw, strict=True)
    except (
        UnicodeError,
        ValueError,
        TypeError,
        binascii.Error,
        JSONDecodeError,
        ValidationError,
        OverflowError,
    ) as error:
        raise InvalidCursor from error

    if payload.query != query.query:
        raise InvalidCursor
    return _ParentCursorPosition(
        serial=payload.serial,
        task_id=str(payload.id),
    )


def _encode_parent_cursor(task: Task, query: ParentOptionQuery) -> str:
    raw = _ParentCursorPayload(
        v=1,
        query=query.query,
        serial=task.serial,
        id=UUID(task.id),
    ).model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _parent_serial_query(value: str | None) -> int | None:
    """仅把 ASCII 数字或井号加 ASCII 数字解释为精确 serial 查询。"""

    if value is None:
        return None
    digits = value[1:] if value.startswith("#") else value
    if not digits.isascii() or not digits.isdigit():
        return None
    return int(digits)


def list_topics(session: Session, user_id: str) -> list[str]:
    """返回当前用户的精确主题集合，并按不区分大小写的展示顺序排列。"""

    topics = session.scalars(
        select(Task.topic).where(Task.user_id == user_id)
    ).all()
    # 数据库 collation 的 distinct 和大小写排序跨方言并不一致；Python 精确 set
    # 保留大小写不同的原值，再用 Unicode casefold 与原值决胜得到稳定展示顺序。
    return sorted(
        set(topics),
        key=lambda value: (value.casefold(), value),
    )


def list_parent_options(
    session: Session,
    user_id: str,
    query: ParentOptionQuery,
) -> ParentOptionPage:
    """分页检索当前用户根任务，供一层父待办关系选择使用。"""

    statement = select(Task).where(
        Task.user_id == user_id,
        Task.parent_id.is_(None),
    )
    serial_query = _parent_serial_query(query.query)
    if query.query is not None:
        if serial_query is None:
            statement = statement.where(
                Task.title.icontains(query.query, autoescape=True)
            )
        elif serial_query > _SQLITE_MAX_INTEGER:
            # 数据库存储不可能出现该编号，直接构造空条件避免驱动层整数溢出。
            statement = statement.where(false())
        else:
            statement = statement.where(Task.serial == serial_query)

    if query.cursor is not None:
        cursor = _decode_parent_cursor(query.cursor, query)
        statement = statement.where(
            or_(
                Task.serial > cursor.serial,
                and_(Task.serial == cursor.serial, Task.id > cursor.task_id),
            )
        )

    statement = statement.order_by(Task.serial.asc(), Task.id.asc())
    rows = list(session.scalars(statement.limit(query.limit + 1)).all())
    has_more = len(rows) > query.limit
    items = rows[: query.limit]
    next_cursor = _encode_parent_cursor(items[-1], query) if has_more else None
    return ParentOptionPage(items=items, next_cursor=next_cursor)


def _allocate_serial(session: Session, user_id: str) -> int:
    """原子推进账号计数器并返回旧值，同时取得该账号的关系写锁。"""

    next_serial = session.scalar(
        update(User)
        .where(User.id == user_id)
        .values(
            next_task_serial=User.next_task_serial + 1,
            updated_at=User.updated_at,
        )
        .returning(User.next_task_serial)
    )
    if next_serial is None:
        raise TaskNotFound
    return next_serial - 1


def _lock_user_for_task_relationship(session: Session, user_id: str) -> None:
    """用原子 no-op UPDATE 串行化同账号父子写入，但不推进流水号。"""

    locked_user_id = session.scalar(
        update(User)
        .where(User.id == user_id)
        .values(
            next_task_serial=User.next_task_serial,
            updated_at=User.updated_at,
        )
        .returning(User.id)
    )
    if locked_user_id is None:
        raise TaskNotFound


def _require_valid_parent(
    session: Session,
    user_id: str,
    parent_id: str,
    *,
    task_id: str | None = None,
) -> Task:
    """要求父任务属于当前账号、位于根层且不是任务自身。"""

    parent = session.scalar(
        select(Task).where(Task.id == parent_id, Task.user_id == user_id)
    )
    if parent is None or parent.parent_id is not None or parent.id == task_id:
        raise InvalidTaskRelationship
    return parent


def create_task(session: Session, user_id: str, payload: TaskCreateRequest) -> Task:
    """在单一事务中分配账号流水号、校验父级并创建 new 状态任务。"""

    try:
        # serial UPDATE 既分配编号也先取得账号写锁；之后必须在锁内重新读取父级。
        serial = _allocate_serial(session, user_id)
        if payload.parent_id is not None:
            _require_valid_parent(session, user_id, payload.parent_id)
        now = utc_now()
        task = Task(
            user_id=user_id,
            serial=serial,
            title=payload.title,
            description=payload.description,  # type: ignore[arg-type]
            priority=(
                payload.priority.value if payload.priority is not None else None
            ),
            topic=payload.topic,
            status="new",
            due_at=payload.due_at,
            completed_at=None,
            parent_id=payload.parent_id,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.commit()
        return task
    except Exception:
        # 计数器、父级校验和 INSERT 共用事务，任何失败都不得消耗流水号。
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


def get_task_detail(
    session: Session,
    user_id: str,
    task_id: str,
) -> TaskDetail:
    """读取当前用户任务及其按编号排序的直接子任务。"""

    task = get_task(session, user_id, task_id)
    # 子查询必须再次绑定当前用户，不能让异常的跨账号 parent_id 引用进入响应。
    children = list(
        session.scalars(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.parent_id == task.id,
            )
            .order_by(Task.serial.asc())
        ).all()
    )
    return TaskDetail(task=task, children=children)


def update_task(
    session: Session,
    user_id: str,
    task_id: str,
    payload: TaskUpdateRequest,
) -> Task:
    """最小化更新显式字段，并原子维护层级、状态时间和更新时间。"""

    try:
        fields = payload.model_fields_set
        if "parent_id" in fields:
            # 先锁账号，再按目标任务、父任务、子任务顺序读取并校验。
            _lock_user_for_task_relationship(session, user_id)
        task = get_task(session, user_id, task_id)

        # 关系校验先于字段赋值；异常仍统一回滚，避免调用方复用 Session 时残留脏状态。
        if "parent_id" in fields:
            if payload.parent_id is not None:
                _require_valid_parent(
                    session,
                    user_id,
                    payload.parent_id,
                    task_id=task.id,
                )
                has_children = session.scalar(
                    select(Task.id)
                    .where(
                        Task.user_id == user_id,
                        Task.parent_id == task.id,
                    )
                    .limit(1)
                )
                if has_children is not None:
                    raise InvalidTaskRelationship
            task.parent_id = payload.parent_id

        if "title" in fields:
            task.title = payload.title  # type: ignore[assignment]
        if "description" in fields:
            task.description = payload.description  # type: ignore[assignment]
        if "priority" in fields:
            task.priority = (
                payload.priority.value if payload.priority is not None else None
            )
        if "topic" in fields:
            task.topic = payload.topic  # type: ignore[assignment]
        if "due_at" in fields:
            task.due_at = payload.due_at

        now = utc_now()
        if "status" in fields:
            next_status = payload.status.value  # type: ignore[union-attr]
            # 重复 completed 保留首次时间；离开 completed 必须清空以支持再次完成。
            if next_status == "completed" and task.status != "completed":
                task.completed_at = now
            elif next_status != "completed":
                task.completed_at = None
            task.status = next_status

        task.updated_at = now
        session.commit()
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


def _matches_task(task: Task, query: TaskListQuery) -> bool:
    """按状态与大小写敏感主题的 AND 语义判断单个任务。"""

    status_matches = (
        query.status is TaskStatusFilter.ALL or task.status == query.status.value
    )
    topic_matches = query.topic is None or task.topic == query.topic
    return status_matches and topic_matches


def list_tasks(
    session: Session,
    user_id: str,
    query: TaskListQuery,
) -> TaskPage:
    """按根任务筛选和分页，并批量组装一层子任务上下文。"""

    # 根任务和直接子任务分别应用相同 AND 条件，再以 OR 决定根分组资格。
    child_match = aliased(Task)
    root_predicates = []
    child_predicates = []
    if query.status is not TaskStatusFilter.ALL:
        root_predicates.append(Task.status == query.status.value)
        child_predicates.append(child_match.status == query.status.value)
    if query.topic is not None:
        root_predicates.append(Task.topic == query.topic)
        child_predicates.append(child_match.topic == query.topic)
    root_matches_filter = (
        and_(*root_predicates) if root_predicates else true()
    )
    matching_child_exists = exists(
        select(child_match.id).where(
            child_match.user_id == user_id,
            child_match.parent_id == Task.id,
            *child_predicates,
        )
    )
    statement = select(Task).where(
        Task.user_id == user_id,
        Task.parent_id.is_(None),
        or_(root_matches_filter, matching_child_exists),
    )

    priority_expression = case(_PRIORITY_RANK, value=Task.priority, else_=0)
    direction = asc if query.order is SortOrder.ASC else desc
    if query.sort is TaskSort.SERIAL:
        sort_expression = Task.serial
        statement = statement.order_by(
            direction(Task.serial),
            direction(Task.id),
        )
    elif query.sort is TaskSort.DUE_AT:
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
            asc(Task.priority.is_(None)),
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
        elif query.sort is TaskSort.PRIORITY and cursor.null_bucket:
            id_predicate = (
                Task.id > cursor.task_id
                if query.order is SortOrder.ASC
                else Task.id < cursor.task_id
            )
            statement = statement.where(Task.priority.is_(None), id_predicate)
        elif query.sort is TaskSort.PRIORITY:
            if type(cursor.value) is not int:
                raise InvalidCursor
            statement = statement.where(
                or_(
                    Task.priority.is_(None),
                    and_(
                        Task.priority.is_not(None),
                        _after_value(
                            priority_expression,
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

    # limit 只作用于根任务；多取一根仅判断下一页，不允许子任务拆分分组。
    roots = list(session.scalars(statement.limit(query.limit + 1)).all())
    has_more = len(roots) > query.limit
    page_roots = roots[: query.limit]
    root_ids = [task.id for task in page_roots]

    # 第二条查询一次性加载当页所有直接子任务，避免按根任务逐条查询。
    child_rows = (
        list(
            session.scalars(
                select(Task)
                .where(
                    Task.user_id == user_id,
                    Task.parent_id.in_(root_ids),
                )
                .order_by(Task.serial.asc())
            ).all()
        )
        if root_ids
        else []
    )
    children_by_parent: dict[str, list[Task]] = {
        root_id: [] for root_id in root_ids
    }
    for child in child_rows:
        if child.parent_id is not None:
            children_by_parent[child.parent_id].append(child)

    groups: list[TaskGroup] = []
    for root in page_roots:
        root_matches = _matches_task(root, query)
        children = children_by_parent[root.id]
        groups.append(
            TaskGroup(
                task=root,
                children=(
                    children
                    if root_matches
                    else [
                        child for child in children if _matches_task(child, query)
                    ]
                ),
                child_count=len(children),
                completed_child_count=sum(
                    child.status == TaskStatus.COMPLETED.value for child in children
                ),
                context_only=not root_matches,
            )
        )

    next_cursor = _encode_cursor(page_roots[-1], query) if has_more else None
    return TaskPage(items=groups, next_cursor=next_cursor)


def delete_task(session: Session, user_id: str, task_id: str) -> None:
    """硬删除当前用户任务；跨用户与不存在使用相同异常。"""

    try:
        task = get_task(session, user_id, task_id)
        # 删除只会移除父边，子任务由数据库 SET NULL 提升，不会制造环或二层关系。
        session.delete(task)
        session.commit()
    except Exception:
        # 查询、删除或提交任一环节失败都恢复到调用前的持久化状态。
        session.rollback()
        raise
