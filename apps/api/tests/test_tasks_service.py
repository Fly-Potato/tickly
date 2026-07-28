import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import create_engine_for_settings, create_session_factory
from app.models import Task, User
from app.schemas.tasks import (
    SortOrder,
    TaskCreateRequest,
    TaskListQuery,
    TaskSort,
    TaskStatus,
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


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database_path = tmp_path / "tasks-service.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    with create_session_factory(engine)() as database_session:
        yield database_session
    engine.dispose()


def add_user(session: Session, username: str) -> User:
    user = User(username=username, password_hash="test-hash")
    session.add(user)
    session.commit()
    return user


def add_task(
    session: Session,
    user_id: str,
    task_id: str,
    title: str,
    *,
    created_at: datetime,
    due_at: datetime | None = None,
    priority: str = "none",
    completed: bool = False,
) -> Task:
    task = Task(
        id=task_id,
        user_id=user_id,
        title=title,
        created_at=created_at,
        updated_at=created_at,
        due_at=due_at,
        priority=priority,
        is_completed=completed,
        completed_at=created_at if completed else None,
    )
    session.add(task)
    session.commit()
    return task


def test_create_and_get_task_are_bound_to_the_owner(session: Session) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")

    created = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="  我的任务  ", priority="high"),
    )

    assert created.user_id == owner.id
    assert created.title == "我的任务"
    assert created.priority == "high"
    assert get_task(session, owner.id, created.id).id == created.id
    with pytest.raises(TaskNotFound):
        get_task(session, other.id, created.id)
    with pytest.raises(TaskNotFound):
        get_task(session, owner.id, "not-a-uuid")


def test_delete_task_is_hard_delete_and_uses_the_same_not_found(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")
    task = create_task(session, owner.id, TaskCreateRequest(title="删除我"))

    with pytest.raises(TaskNotFound):
        delete_task(session, other.id, task.id)
    assert session.scalar(select(Task.id).where(Task.id == task.id)) == task.id

    delete_task(session, owner.id, task.id)
    assert session.scalar(select(Task.id).where(Task.id == task.id)) is None
    with pytest.raises(TaskNotFound):
        delete_task(session, owner.id, task.id)


def test_create_and_delete_roll_back_when_commit_fails(session: Session) -> None:
    owner = add_user(session, "owner")
    existing = create_task(session, owner.id, TaskCreateRequest(title="保留任务"))

    session.add(Task(user_id=owner.id, title=""))
    with pytest.raises(IntegrityError):
        create_task(session, owner.id, TaskCreateRequest(title="不应创建"))
    assert session.scalar(select(Task.id).where(Task.title == "不应创建")) is None

    session.add(Task(user_id=owner.id, title=""))
    with pytest.raises(IntegrityError):
        delete_task(session, owner.id, existing.id)
    session.expire_all()
    assert get_task(session, owner.id, existing.id).title == "保留任务"


def test_update_task_controls_completed_at_and_explicit_clearing(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    task = create_task(
        session,
        owner.id,
        TaskCreateRequest(
            title="原任务",
            notes="备注",
            due_at="2026-07-30T18:00:00+08:00",
        ),
    )

    completed = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(title="新任务", notes=None, due_at=None, is_completed=True),
    )
    first_completed_at = completed.completed_at
    first_updated_at = completed.updated_at

    assert completed.title == "新任务"
    assert completed.notes is None
    assert completed.due_at is None
    assert completed.is_completed is True
    assert first_completed_at is not None

    preserved = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(title="新任务"),
    )
    assert preserved.is_completed is True
    assert preserved.completed_at == first_completed_at
    assert preserved.updated_at != first_updated_at

    repeated = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(is_completed=True),
    )
    assert repeated.completed_at == first_completed_at

    reopened = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(is_completed=False),
    )
    assert reopened.is_completed is False
    assert reopened.completed_at is None


def test_update_task_rolls_back_all_pending_changes_on_commit_failure(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    task = create_task(session, owner.id, TaskCreateRequest(title="原标题"))
    session.add(Task(user_id=owner.id, title=""))

    with pytest.raises(IntegrityError):
        update_task(
            session,
            owner.id,
            task.id,
            TaskUpdateRequest(title="不应提交"),
        )

    session.expire_all()
    assert get_task(session, owner.id, task.id).title == "原标题"
    assert session.scalar(select(Task.id).where(Task.title == "")) is None


def test_list_tasks_filters_owner_status_and_uses_stable_cursor(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    for index in range(4):
        add_task(
            session,
            owner.id,
            f"00000000-0000-0000-0000-{index + 1:012d}",
            f"owner-{index}",
            created_at=now,
            completed=index == 0,
        )
    add_task(
        session,
        other.id,
        "00000000-0000-0000-0000-999999999999",
        "other",
        created_at=now + timedelta(days=1),
    )

    first = list_tasks(
        session,
        owner.id,
        TaskListQuery(status=TaskStatus.ACTIVE, limit=2),
    )
    second = list_tasks(
        session,
        owner.id,
        TaskListQuery(
            status=TaskStatus.ACTIVE,
            limit=2,
            cursor=first.next_cursor,
        ),
    )

    assert [task.title for task in first.items] == ["owner-3", "owner-2"]
    assert [task.title for task in second.items] == ["owner-1"]
    assert first.next_cursor is not None
    assert second.next_cursor is None

    completed = list_tasks(
        session,
        owner.id,
        TaskListQuery(status=TaskStatus.COMPLETED),
    )
    all_tasks = list_tasks(
        session,
        owner.id,
        TaskListQuery(status=TaskStatus.ALL),
    )
    assert [task.title for task in completed.items] == ["owner-0"]
    assert [task.title for task in all_tasks.items] == [
        "owner-3",
        "owner-2",
        "owner-1",
        "owner-0",
    ]


def test_due_at_null_is_last_and_priority_uses_explicit_rank(session: Session) -> None:
    owner = add_user(session, "owner")
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000001",
        "none",
        created_at=now,
        priority="none",
        due_at=None,
    )
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000002",
        "low",
        created_at=now,
        priority="low",
        due_at=now + timedelta(days=2),
    )
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000003",
        "high",
        created_at=now,
        priority="high",
        due_at=now + timedelta(days=1),
    )

    due_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(sort=TaskSort.DUE_AT, order=SortOrder.DESC, limit=2),
    )
    due_tail = list_tasks(
        session,
        owner.id,
        TaskListQuery(
            sort=TaskSort.DUE_AT,
            order=SortOrder.DESC,
            limit=2,
            cursor=due_page.next_cursor,
        ),
    )
    priority_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(sort=TaskSort.PRIORITY, order=SortOrder.DESC),
    )

    assert [task.title for task in due_page.items] == ["low", "high"]
    assert [task.title for task in due_tail.items] == ["none"]
    assert [task.title for task in priority_page.items] == ["high", "low", "none"]


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        (TaskSort.CREATED_AT, SortOrder.ASC, ["none", "low", "high"]),
        (TaskSort.CREATED_AT, SortOrder.DESC, ["high", "low", "none"]),
        (TaskSort.DUE_AT, SortOrder.ASC, ["high", "low", "none"]),
        (TaskSort.DUE_AT, SortOrder.DESC, ["low", "high", "none"]),
        (TaskSort.PRIORITY, SortOrder.ASC, ["none", "low", "high"]),
        (TaskSort.PRIORITY, SortOrder.DESC, ["high", "low", "none"]),
    ],
)
def test_all_sort_modes_support_both_directions(
    session: Session,
    sort: TaskSort,
    order: SortOrder,
    expected: list[str],
) -> None:
    owner = add_user(session, f"user_{sort.value}_{order.value}")
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000001",
        "none",
        created_at=now,
        priority="none",
        due_at=None,
    )
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000002",
        "low",
        created_at=now + timedelta(minutes=1),
        priority="low",
        due_at=now + timedelta(days=2),
    )
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000003",
        "high",
        created_at=now + timedelta(minutes=2),
        priority="high",
        due_at=now + timedelta(days=1),
    )

    page = list_tasks(
        session,
        owner.id,
        TaskListQuery(sort=sort, order=order),
    )

    assert [task.title for task in page.items] == expected


def test_cursor_rejects_damage_and_cross_query_reuse(session: Session) -> None:
    owner = add_user(session, "owner")
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    for index in range(2):
        add_task(
            session,
            owner.id,
            f"00000000-0000-0000-0000-{index + 1:012d}",
            str(index),
            created_at=now + timedelta(minutes=index),
        )
    page = list_tasks(session, owner.id, TaskListQuery(limit=1))
    assert page.next_cursor is not None
    padding = "=" * (-len(page.next_cursor) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode((page.next_cursor + padding).encode("ascii"))
    )
    payload["v"] = 2
    unknown_version = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    payload["v"] = 1
    payload["unexpected"] = "value"
    extra_field = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )

    with pytest.raises(InvalidCursor):
        list_tasks(session, owner.id, TaskListQuery(cursor="not-base64"))
    with pytest.raises(InvalidCursor):
        list_tasks(session, owner.id, TaskListQuery(cursor=unknown_version))
    with pytest.raises(InvalidCursor):
        list_tasks(session, owner.id, TaskListQuery(cursor=extra_field))
    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(status=TaskStatus.ACTIVE, cursor=page.next_cursor),
        )
