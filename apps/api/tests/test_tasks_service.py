import base64
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import NO_VALUE

import app.services.tasks as tasks_service
from app.db.session import create_engine_for_settings, create_session_factory
from app.models import Task, User
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
from app.services.tasks import (
    InvalidCursor,
    TaskNotFound,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)


def create_test_database(
    tmp_path: Path, filename: str = "tasks-service.db"
) -> tuple[Engine, sessionmaker[Session]]:
    """创建完成 migration 的文件数据库，供单 Session 与真实并发测试复用。"""

    database_path = tmp_path / filename
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    return engine, create_session_factory(engine)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine, session_factory = create_test_database(tmp_path)
    with session_factory() as database_session:
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
    serial: int,
    created_at: datetime,
    description: str | None = None,
    topic: str = "Tickly",
    status: str = "new",
    parent_id: str | None = None,
    due_at: datetime | None = None,
    priority: str | None = None,
) -> Task:
    task = Task(
        id=task_id,
        user_id=user_id,
        serial=serial,
        title=title,
        description=title if description is None else description,
        topic=topic,
        status=status,
        parent_id=parent_id,
        created_at=created_at,
        updated_at=created_at,
        due_at=due_at,
        priority=priority,
        completed_at=created_at if status == "completed" else None,
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
        TaskCreateRequest(title="  我的任务  ", topic="Tickly", priority="high"),
    )

    assert created.user_id == owner.id
    assert created.serial == 1
    assert created.title == "我的任务"
    assert created.description == "我的任务"
    assert created.priority == "high"
    assert created.topic == "Tickly"
    assert created.status == "new"
    assert created.completed_at is None
    assert get_task(session, owner.id, created.id).id == created.id
    with pytest.raises(TaskNotFound):
        get_task(session, other.id, created.id)
    with pytest.raises(TaskNotFound):
        get_task(session, owner.id, "not-a-uuid")


def test_create_returns_complete_object_without_post_commit_refresh(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")

    def reject_refresh(*_: object, **__: object) -> None:
        raise AssertionError("commit 成功后不得再 refresh 并制造假失败")

    monkeypatch.setattr(session, "refresh", reject_refresh)
    created = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="无需刷新", topic="Tickly"),
    )

    assert created.id
    assert created.serial == 1
    assert created.title == "无需刷新"
    assert created.description == "无需刷新"
    assert created.status == "new"
    assert created.created_at is not None
    assert created.updated_at is not None


def test_serial_is_per_user_monotonic_and_delete_does_not_reuse(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")

    owner_first = create_task(
        session, owner.id, TaskCreateRequest(title="owner-1", topic="Tickly")
    )
    other_first = create_task(
        session, other.id, TaskCreateRequest(title="other-1", topic="Tickly")
    )
    owner_second = create_task(
        session, owner.id, TaskCreateRequest(title="owner-2", topic="Tickly")
    )

    assert (owner_first.serial, owner_second.serial) == (1, 2)
    assert other_first.serial == 1

    delete_task(session, owner.id, owner_second.id)
    owner_third = create_task(
        session, owner.id, TaskCreateRequest(title="owner-3", topic="Tickly")
    )
    assert owner_third.serial == 3

    with pytest.raises(TaskNotFound):
        create_task(
            session,
            "00000000-0000-0000-0000-000000000000",
            TaskCreateRequest(title="无账号任务", topic="Tickly"),
        )
    owner_fourth = create_task(
        session, owner.id, TaskCreateRequest(title="owner-4", topic="Tickly")
    )
    assert owner_fourth.serial == 4


def test_concurrent_creates_allocate_unique_continuous_serials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(
        tmp_path, "tasks-service-concurrent-serial.db"
    )
    first_lock_held = Event()
    second_sql_attempted = Event()
    allocation_lock = Lock()
    allocation_call_count = 0
    serial_sql_count = 0
    original_allocate_serial = tasks_service._allocate_serial

    def observe_serial_allocation_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        """在第二条流水号 UPDATE 进入驱动前记录真实锁竞争。"""

        normalized_statement = " ".join(statement.upper().split())
        if not (
            normalized_statement.startswith("UPDATE USERS SET")
            and "NEXT_TASK_SERIAL" in normalized_statement
            and "RETURNING NEXT_TASK_SERIAL" in normalized_statement
        ):
            return

        nonlocal serial_sql_count
        with allocation_lock:
            serial_sql_count += 1
            if serial_sql_count == 2:
                assert first_lock_held.is_set(), (
                    "第二条流水号 SQL 必须在首个事务持锁时进入"
                )
                second_sql_attempted.set()

    def allocate_with_real_lock_contention(
        database_session: Session, user_id: str
    ) -> int:
        """首事务持锁等待第二条 SQL，避免以线程启动顺序冒充数据库竞争。"""

        nonlocal allocation_call_count
        with allocation_lock:
            allocation_call_count += 1
            call_number = allocation_call_count

        if call_number == 1:
            serial = original_allocate_serial(database_session, user_id)
            first_lock_held.set()
            assert second_sql_attempted.wait(timeout=5), (
                "第二个事务未在首个事务提交前尝试流水号 UPDATE"
            )
            return serial

        assert call_number == 2, "本测试只允许两次流水号分配"
        assert first_lock_held.wait(timeout=5), "第二个事务等待首个事务持锁超时"
        return original_allocate_serial(database_session, user_id)

    event.listen(engine, "before_cursor_execute", observe_serial_allocation_sql)
    try:
        with session_factory() as setup_session:
            owner = add_user(setup_session, "owner")
            owner_id = owner.id

        start = Barrier(2)

        def create_concurrently(title: str) -> int:
            with session_factory() as worker_session:
                start.wait(timeout=5)
                return create_task(
                    worker_session,
                    owner_id,
                    TaskCreateRequest(title=title, topic="Tickly"),
                ).serial

        with monkeypatch.context() as allocation_patch:
            allocation_patch.setattr(
                tasks_service, "_allocate_serial", allocate_with_real_lock_contention
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(create_concurrently, "并发任务一"),
                    executor.submit(create_concurrently, "并发任务二"),
                ]
                serials = [future.result(timeout=10) for future in futures]

        with session_factory() as verification_session:
            persisted_owner = verification_session.get(User, owner_id)
            task_count = verification_session.scalar(
                select(func.count()).select_from(Task).where(Task.user_id == owner_id)
            )
            assert persisted_owner is not None
            assert first_lock_held.is_set()
            assert second_sql_attempted.is_set()
            assert serial_sql_count == 2
            assert sorted(serials) == [1, 2]
            assert task_count == 2
            assert persisted_owner.next_task_serial == 3
    finally:
        event.remove(engine, "before_cursor_execute", observe_serial_allocation_sql)
        engine.dispose()


def test_default_description_is_not_coupled_to_later_title_changes(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    task = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="原始标题", description="   ", topic="Tickly"),
    )
    assert task.description == "原始标题"

    updated = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(title="修改标题"),
    )
    assert updated.title == "修改标题"
    assert updated.description == "原始标题"


def test_update_returns_complete_object_without_post_commit_refresh(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")
    task = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="更新前", topic="Tickly"),
    )

    def reject_refresh(*_: object, **__: object) -> None:
        raise AssertionError("commit 成功后不得再 refresh 并制造假失败")

    monkeypatch.setattr(session, "refresh", reject_refresh)
    updated = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(
            title="更新后",
            description="完整返回",
            priority="high",
            topic="Work",
            status="in_progress",
        ),
    )

    assert updated.id == task.id
    assert updated.serial == task.serial
    assert updated.title == "更新后"
    assert updated.description == "完整返回"
    assert updated.priority == "high"
    assert updated.topic == "Work"
    assert updated.status == "in_progress"
    assert updated.updated_at is not None


def test_delete_task_is_hard_delete_and_uses_the_same_not_found(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")
    task = create_task(
        session, owner.id, TaskCreateRequest(title="删除我", topic="Tickly")
    )

    with pytest.raises(TaskNotFound):
        delete_task(session, other.id, task.id)
    assert session.scalar(select(Task.id).where(Task.id == task.id)) == task.id

    delete_task(session, owner.id, task.id)
    assert session.scalar(select(Task.id).where(Task.id == task.id)) is None
    with pytest.raises(TaskNotFound):
        delete_task(session, owner.id, task.id)


def test_create_and_delete_roll_back_when_commit_fails(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")
    existing = create_task(
        session, owner.id, TaskCreateRequest(title="保留任务", topic="Tickly")
    )

    def fail_commit() -> None:
        raise IntegrityError("强制提交失败", {}, RuntimeError("测试事务回滚"))

    with monkeypatch.context() as commit_failure:
        commit_failure.setattr(session, "commit", fail_commit)
        with pytest.raises(IntegrityError):
            create_task(
                session,
                owner.id,
                TaskCreateRequest(title="不应创建", topic="Tickly"),
            )
    assert session.scalar(select(Task.id).where(Task.title == "不应创建")) is None
    session.refresh(owner)
    assert owner.next_task_serial == 2

    after_failure = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="回滚后可创建", topic="Tickly"),
    )
    assert after_failure.serial == 2

    with monkeypatch.context() as commit_failure:
        commit_failure.setattr(session, "commit", fail_commit)
        with pytest.raises(IntegrityError):
            delete_task(session, owner.id, existing.id)
    session.expire_all()
    assert get_task(session, owner.id, existing.id).title == "保留任务"


def test_create_rolls_back_real_unique_violation_and_serial_counter(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")
    existing = create_task(
        session, owner.id, TaskCreateRequest(title="占用流水号", topic="Tickly")
    )
    original_allocate_serial = tasks_service._allocate_serial

    def allocate_occupied_serial(database_session: Session, user_id: str) -> int:
        # 先真实推进计数器，再返回已占用值，让数据库唯一约束在 commit 时拒绝。
        original_allocate_serial(database_session, user_id)
        return existing.serial

    with monkeypatch.context() as collision:
        collision.setattr(
            tasks_service, "_allocate_serial", allocate_occupied_serial
        )
        with pytest.raises(IntegrityError):
            create_task(
                session,
                owner.id,
                TaskCreateRequest(title="流水号冲突", topic="Tickly"),
            )

    session.refresh(owner)
    assert owner.next_task_serial == 2
    assert session.scalar(
        select(func.count()).select_from(Task).where(Task.user_id == owner.id)
    ) == 1

    recovered = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="冲突后可创建", topic="Tickly"),
    )
    assert recovered.serial == 2


def test_status_transitions_control_completed_at(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")
    task = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000001",
        "原任务",
        serial=1,
        created_at=datetime(2026, 8, 17, 8, tzinfo=UTC),
        description="备注",
    )

    in_progress_time = datetime(2026, 8, 17, 9, tzinfo=UTC)
    completed_time = datetime(2026, 8, 17, 10, tzinfo=UTC)
    repeated_time = datetime(2026, 8, 17, 11, tzinfo=UTC)
    reopened_time = datetime(2026, 8, 17, 12, tzinfo=UTC)
    completed_again_time = datetime(2026, 8, 17, 13, tzinfo=UTC)

    monkeypatch.setattr("app.services.tasks.utc_now", lambda: in_progress_time)
    in_progress = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="in_progress"),
    )
    assert in_progress.status == "in_progress"
    assert in_progress.completed_at is None
    assert in_progress.updated_at == in_progress_time

    monkeypatch.setattr("app.services.tasks.utc_now", lambda: completed_time)
    completed = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="completed"),
    )
    assert completed.status == "completed"
    assert completed.completed_at == completed_time
    assert completed.updated_at == completed_time

    monkeypatch.setattr("app.services.tasks.utc_now", lambda: repeated_time)
    repeated = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="completed"),
    )
    assert repeated.completed_at == completed_time
    assert repeated.updated_at == repeated_time

    monkeypatch.setattr("app.services.tasks.utc_now", lambda: reopened_time)
    reopened = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="new"),
    )
    assert reopened.status == "new"
    assert reopened.completed_at is None
    assert reopened.updated_at == reopened_time

    monkeypatch.setattr("app.services.tasks.utc_now", lambda: completed_again_time)
    completed_again = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="completed"),
    )
    assert completed_again.status == "completed"
    assert completed_again.completed_at == completed_again_time
    assert completed_again.updated_at == completed_again_time


def test_update_task_rolls_back_all_pending_changes_on_commit_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")
    task = create_task(
        session,
        owner.id,
        TaskCreateRequest(
            title="原标题", description="原描述", topic="Tickly"
        ),
    )
    original_updated_at = task.updated_at.replace(tzinfo=None)

    def fail_commit() -> None:
        raise IntegrityError("强制提交失败", {}, RuntimeError("测试事务回滚"))

    with monkeypatch.context() as commit_failure:
        commit_failure.setattr(session, "commit", fail_commit)
        with pytest.raises(IntegrityError):
            update_task(
                session,
                owner.id,
                task.id,
                TaskUpdateRequest(
                    title="不应提交",
                    description="不应保留",
                    status="completed",
                ),
            )

    session.expire_all()
    persisted = get_task(session, owner.id, task.id)
    assert persisted.title == "原标题"
    assert persisted.description == "原描述"
    assert persisted.status == "new"
    assert persisted.completed_at is None
    assert persisted.updated_at == original_updated_at

    recovered = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(title="回滚后可更新"),
    )
    assert recovered.title == "回滚后可更新"


def test_update_rolls_back_real_check_violation_and_session_recovers(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    task = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="合法标题", description="合法描述", topic="Tickly"),
    )
    original_updated_at = task.updated_at.replace(tzinfo=None)
    invalid_payload = TaskUpdateRequest.model_construct(
        title="",
        description="不应保留",
        status=TaskStatus.COMPLETED,
    )

    with pytest.raises(IntegrityError):
        update_task(session, owner.id, task.id, invalid_payload)

    session.expire_all()
    persisted = get_task(session, owner.id, task.id)
    assert persisted.title == "合法标题"
    assert persisted.description == "合法描述"
    assert persisted.status == "new"
    assert persisted.completed_at is None
    assert persisted.updated_at == original_updated_at

    recovered = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(title="约束失败后可更新"),
    )
    assert recovered.title == "约束失败后可更新"


def test_update_task_only_patches_explicit_mutable_fields(session: Session) -> None:
    owner = add_user(session, "owner")
    root = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000001",
        "父任务",
        serial=1,
        created_at=datetime(2026, 8, 17, 8, tzinfo=UTC),
    )
    task = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000002",
        "原标题",
        serial=2,
        created_at=datetime(2026, 8, 17, 9, tzinfo=UTC),
        description="原描述",
        priority="low",
        parent_id=root.id,
    )
    immutable_values = (
        task.id,
        task.serial,
        task.user_id,
        task.created_at,
    )
    original_next_task_serial = owner.next_task_serial
    original_user_updated_at = owner.updated_at.replace(tzinfo=None)
    due_at = datetime(2026, 8, 20, 8, tzinfo=UTC)

    updated = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(
            title="新标题",
            description="新描述",
            priority=None,
            topic="Work",
            due_at=due_at,
            parent_id=None,
            status="in_progress",
        ),
    )

    assert (updated.id, updated.serial, updated.user_id, updated.created_at) == (
        immutable_values
    )
    assert updated.title == "新标题"
    assert updated.description == "新描述"
    assert updated.priority is None
    assert updated.topic == "Work"
    assert updated.due_at == due_at
    assert updated.parent_id is None
    assert updated.status == "in_progress"
    assert updated.completed_at is None
    session.refresh(owner)
    assert owner.next_task_serial == original_next_task_serial
    assert owner.updated_at == original_user_updated_at


def test_create_rejects_cross_user_or_child_parent_without_consuming_serial(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")
    root = create_task(
        session, owner.id, TaskCreateRequest(title="父任务", topic="Tickly")
    )
    child = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="子任务", topic="Tickly", parent_id=root.id),
    )
    other_root = create_task(
        session, other.id, TaskCreateRequest(title="其他账号父任务", topic="Tickly")
    )

    with pytest.raises(tasks_service.InvalidTaskRelationship):
        create_task(
            session,
            owner.id,
            TaskCreateRequest(
                title="父任务不存在",
                topic="Tickly",
                parent_id="00000000-0000-0000-0000-999999999999",
            ),
        )
    with pytest.raises(tasks_service.InvalidTaskRelationship):
        create_task(
            session,
            owner.id,
            TaskCreateRequest(
                title="跨账号子任务", topic="Tickly", parent_id=other_root.id
            ),
        )
    with pytest.raises(tasks_service.InvalidTaskRelationship):
        create_task(
            session,
            owner.id,
            TaskCreateRequest(title="二层任务", topic="Tickly", parent_id=child.id),
        )

    session.refresh(owner)
    assert owner.next_task_serial == 3
    recovered = create_task(
        session, owner.id, TaskCreateRequest(title="回滚后创建", topic="Tickly")
    )
    assert recovered.serial == 3


def test_invalid_parent_updates_roll_back_all_fields_and_leave_session_usable(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    other = add_user(session, "other")
    root_with_child = create_task(
        session, owner.id, TaskCreateRequest(title="已有子任务的根", topic="Tickly")
    )
    child = create_task(
        session,
        owner.id,
        TaskCreateRequest(
            title="现有子任务", topic="Tickly", parent_id=root_with_child.id
        ),
    )
    target_root = create_task(
        session, owner.id, TaskCreateRequest(title="目标根", topic="Tickly")
    )
    other_root = create_task(
        session, other.id, TaskCreateRequest(title="其他账号根", topic="Tickly")
    )
    original_updated_at = root_with_child.updated_at.replace(tzinfo=None)

    with pytest.raises(tasks_service.InvalidTaskRelationship):
        update_task(
            session,
            owner.id,
            target_root.id,
            TaskUpdateRequest(
                title="不存在父任务时不应保留",
                parent_id="00000000-0000-0000-0000-999999999999",
            ),
        )
    with pytest.raises(tasks_service.InvalidTaskRelationship):
        update_task(
            session,
            owner.id,
            target_root.id,
            TaskUpdateRequest(parent_id=target_root.id),
        )
    with pytest.raises(tasks_service.InvalidTaskRelationship):
        update_task(
            session,
            owner.id,
            child.id,
            TaskUpdateRequest(parent_id=other_root.id),
        )
    with pytest.raises(tasks_service.InvalidTaskRelationship):
        update_task(
            session,
            owner.id,
            root_with_child.id,
            TaskUpdateRequest(
                title="不应保留",
                status="completed",
                parent_id=target_root.id,
            ),
        )

    session.expire_all()
    persisted_root = get_task(session, owner.id, root_with_child.id)
    assert persisted_root.title == "已有子任务的根"
    assert persisted_root.status == "new"
    assert persisted_root.completed_at is None
    assert persisted_root.parent_id is None
    assert persisted_root.updated_at == original_updated_at

    promoted = update_task(
        session,
        owner.id,
        child.id,
        TaskUpdateRequest(parent_id=None),
    )
    assert promoted.parent_id is None
    recovered = update_task(
        session,
        owner.id,
        target_root.id,
        TaskUpdateRequest(title="回滚后可更新"),
    )
    assert recovered.title == "回滚后可更新"
    after_relationship_updates = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="关系锁不推进流水号", topic="Tickly"),
    )
    assert after_relationship_updates.serial == 4


def test_parent_delete_promotes_children_and_statuses_do_not_cascade(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    parent = create_task(
        session, owner.id, TaskCreateRequest(title="父任务", topic="Tickly")
    )
    child = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="子任务", topic="Tickly", parent_id=parent.id),
    )

    completed_child = update_task(
        session,
        owner.id,
        child.id,
        TaskUpdateRequest(status="completed"),
    )
    session.refresh(parent)
    assert completed_child.status == "completed"
    assert parent.status == "new"

    update_task(
        session,
        owner.id,
        child.id,
        TaskUpdateRequest(status="new"),
    )
    completed_parent = update_task(
        session,
        owner.id,
        parent.id,
        TaskUpdateRequest(status="completed"),
    )
    session.refresh(child)
    assert completed_parent.status == "completed"
    assert child.status == "new"
    assert [loaded_child.id for loaded_child in parent.children] == [child.id]
    assert inspect(parent).attrs.children.loaded_value is not NO_VALUE

    delete_task(session, owner.id, parent.id)
    session.refresh(child)
    assert child.parent_id is None
    assert child.status == "new"


def test_delete_parent_with_unloaded_child_keeps_and_promotes_child(
    session: Session,
) -> None:
    owner = add_user(session, "owner")
    parent = create_task(
        session, owner.id, TaskCreateRequest(title="父任务", topic="Tickly")
    )
    child = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="未加载子任务", topic="Tickly", parent_id=parent.id),
    )
    parent_id = parent.id
    child_id = child.id
    session.expunge_all()

    unloaded_parent = get_task(session, owner.id, parent_id)
    assert inspect(unloaded_parent).attrs.children.loaded_value is NO_VALUE
    delete_task(session, owner.id, parent_id)

    persisted_child = get_task(session, owner.id, child_id)
    assert persisted_child.parent_id is None
    assert persisted_child.title == "未加载子任务"


def test_concurrent_roots_cannot_become_each_others_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(
        tmp_path, "tasks-service-mutual-parent-race.db"
    )
    try:
        with session_factory() as setup_session:
            owner = add_user(setup_session, "owner")
            root_a = create_task(
                setup_session,
                owner.id,
                TaskCreateRequest(title="根 A", topic="Tickly"),
            )
            root_b = create_task(
                setup_session,
                owner.id,
                TaskCreateRequest(title="根 B", topic="Tickly"),
            )
            owner_id = owner.id
            root_a_id = root_a.id
            root_b_id = root_b.id

        original_validate_parent = tasks_service._require_valid_parent
        validation_release = Event()
        validation_lock = Lock()
        validation_count = 0

        def coordinate_validated_parents(
            database_session: Session,
            user_id: str,
            parent_id: str,
            *,
            task_id: str | None = None,
        ) -> Task:
            parent = original_validate_parent(
                database_session, user_id, parent_id, task_id=task_id
            )
            nonlocal validation_count
            with validation_lock:
                validation_count += 1
                if validation_count == 2:
                    validation_release.set()
            # 旧实现让两路 SELECT 都通过后再写；账号写锁实现则由第一路超时后先提交。
            validation_release.wait(timeout=0.75)
            return parent

        monkeypatch.setattr(
            tasks_service, "_require_valid_parent", coordinate_validated_parents
        )
        start = Barrier(2)

        def move_root(task_id: str, parent_id: str) -> str:
            with session_factory() as worker_session:
                start.wait(timeout=5)
                try:
                    update_task(
                        worker_session,
                        owner_id,
                        task_id,
                        TaskUpdateRequest(parent_id=parent_id),
                    )
                except tasks_service.InvalidTaskRelationship:
                    return "invalid"
                except Exception as error:  # noqa: BLE001 - 测试需暴露数据库并发错误类型
                    return type(error).__name__
                return "ok"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(move_root, root_a_id, root_b_id),
                executor.submit(move_root, root_b_id, root_a_id),
            ]
            outcomes = [future.result(timeout=10) for future in futures]

        with session_factory() as verification_session:
            persisted_a = get_task(verification_session, owner_id, root_a_id)
            persisted_b = get_task(verification_session, owner_id, root_b_id)
            assert sorted(outcomes) == ["invalid", "ok"]
            assert not (
                persisted_a.parent_id == persisted_b.id
                and persisted_b.parent_id == persisted_a.id
            )
            assert sum(
                parent_id is not None
                for parent_id in (persisted_a.parent_id, persisted_b.parent_id)
            ) <= 1
    finally:
        engine.dispose()


def test_concurrent_child_create_and_parent_move_preserve_one_level_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, session_factory = create_test_database(
        tmp_path, "tasks-service-parent-child-race.db"
    )
    try:
        with session_factory() as setup_session:
            owner = add_user(setup_session, "owner")
            parent = create_task(
                setup_session,
                owner.id,
                TaskCreateRequest(title="竞争父任务", topic="Tickly"),
            )
            target_parent = create_task(
                setup_session,
                owner.id,
                TaskCreateRequest(title="目标父任务", topic="Tickly"),
            )
            owner_id = owner.id
            parent_id = parent.id
            target_parent_id = target_parent.id

        original_validate_parent = tasks_service._require_valid_parent
        validation_release = Event()
        validation_lock = Lock()
        validation_count = 0

        def coordinate_validated_parents(
            database_session: Session,
            user_id: str,
            selected_parent_id: str,
            *,
            task_id: str | None = None,
        ) -> Task:
            selected_parent = original_validate_parent(
                database_session,
                user_id,
                selected_parent_id,
                task_id=task_id,
            )
            nonlocal validation_count
            with validation_lock:
                validation_count += 1
                if validation_count == 2:
                    validation_release.set()
            validation_release.wait(timeout=0.75)
            return selected_parent

        monkeypatch.setattr(
            tasks_service, "_require_valid_parent", coordinate_validated_parents
        )
        start = Barrier(2)

        def create_child() -> str:
            with session_factory() as worker_session:
                start.wait(timeout=5)
                try:
                    create_task(
                        worker_session,
                        owner_id,
                        TaskCreateRequest(
                            title="竞争子任务",
                            topic="Tickly",
                            parent_id=parent_id,
                        ),
                    )
                except tasks_service.InvalidTaskRelationship:
                    return "invalid"
                except Exception as error:  # noqa: BLE001 - 测试需暴露数据库并发错误类型
                    return type(error).__name__
                return "ok"

        def move_parent() -> str:
            with session_factory() as worker_session:
                start.wait(timeout=5)
                try:
                    update_task(
                        worker_session,
                        owner_id,
                        parent_id,
                        TaskUpdateRequest(parent_id=target_parent_id),
                    )
                except tasks_service.InvalidTaskRelationship:
                    return "invalid"
                except Exception as error:  # noqa: BLE001 - 测试需暴露数据库并发错误类型
                    return type(error).__name__
                return "ok"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create_child), executor.submit(move_parent)]
            outcomes = [future.result(timeout=10) for future in futures]

        with session_factory() as verification_session:
            persisted_parent = get_task(verification_session, owner_id, parent_id)
            persisted_child = verification_session.scalar(
                select(Task).where(
                    Task.user_id == owner_id,
                    Task.title == "竞争子任务",
                )
            )
            assert sorted(outcomes) == ["invalid", "ok"]
            assert not (
                persisted_parent.parent_id is not None
                and persisted_child is not None
                and persisted_child.parent_id == persisted_parent.id
            )
    finally:
        engine.dispose()


def add_tree_filter_fixture(session: Session) -> tuple[User, dict[int, Task]]:
    """创建同时覆盖根命中、子命中和跨账号隔离的固定任务树。"""

    owner = add_user(session, "tree-owner")
    other = add_user(session, "tree-other")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    tasks = {
        1: add_task(
            session,
            owner.id,
            "00000000-0000-0000-0000-000000000001",
            "root-new",
            serial=1,
            created_at=now,
            topic="Tickly",
            status="new",
        )
    }
    tasks[2] = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000002",
        "child-completed",
        serial=2,
        created_at=now + timedelta(minutes=1),
        topic="Tickly",
        status="completed",
        parent_id=tasks[1].id,
    )
    tasks[3] = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000003",
        "child-in-progress",
        serial=3,
        created_at=now + timedelta(minutes=2),
        topic="Work",
        status="in_progress",
        parent_id=tasks[1].id,
    )
    tasks[4] = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000004",
        "root-completed",
        serial=4,
        created_at=now + timedelta(minutes=3),
        topic="Work",
        status="completed",
    )
    tasks[5] = add_task(
        session,
        other.id,
        "00000000-0000-0000-0000-000000000005",
        "other-user",
        serial=1,
        created_at=now + timedelta(minutes=4),
        topic="Tickly",
        status="new",
    )
    return owner, tasks


def test_tree_filters_roots_or_direct_children_and_keeps_complete_counts(
    session: Session,
) -> None:
    owner, tasks = add_tree_filter_fixture(session)

    new_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(status=TaskStatusFilter.NEW),
    )
    in_progress_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(status=TaskStatusFilter.IN_PROGRESS),
    )
    work_completed_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(topic="Work", status=TaskStatusFilter.COMPLETED),
    )
    tickly_completed_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(topic="Tickly", status=TaskStatusFilter.COMPLETED),
    )
    lowercase_topic_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(topic="tickly"),
    )

    assert [group.task.id for group in new_page.items] == [tasks[1].id]
    assert [child.id for child in new_page.items[0].children] == [
        tasks[2].id,
        tasks[3].id,
    ]
    assert new_page.items[0].context_only is False
    assert new_page.items[0].child_count == 2
    assert new_page.items[0].completed_child_count == 1

    assert [group.task.id for group in in_progress_page.items] == [tasks[1].id]
    assert [child.id for child in in_progress_page.items[0].children] == [tasks[3].id]
    assert in_progress_page.items[0].context_only is True

    assert [group.task.id for group in work_completed_page.items] == [tasks[4].id]
    assert work_completed_page.items[0].children == []
    assert work_completed_page.items[0].context_only is False

    assert [group.task.id for group in tickly_completed_page.items] == [tasks[1].id]
    context_group = tickly_completed_page.items[0]
    assert [child.id for child in context_group.children] == [tasks[2].id]
    assert context_group.context_only is True
    # 上下文子集不能改变进度和删除提示所需的完整直接子任务计数。
    assert context_group.child_count == 2
    assert context_group.completed_child_count == 1
    assert lowercase_topic_page.items == []
    assert lowercase_topic_page.next_cursor is None
    returned_ids = {
        task.id
        for page in (
            new_page,
            in_progress_page,
            work_completed_page,
            tickly_completed_page,
        )
        for group in page.items
        for task in (group.task, *group.children)
    }
    assert tasks[5].id not in returned_ids


def test_tree_list_uses_one_root_query_and_one_batch_child_query(
    session: Session,
) -> None:
    owner, tasks = add_tree_filter_fixture(session)
    statements: list[str] = []

    def record_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", record_select)
    try:
        page = list_tasks(
            session,
            owner.id,
            TaskListQuery(topic="Tickly", status=TaskStatusFilter.COMPLETED),
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_select)

    assert len(statements) == 2
    assert [group.task.id for group in page.items] == [tasks[1].id]
    assert [child.id for child in page.items[0].children] == [tasks[2].id]


def test_cross_user_child_cannot_qualify_or_join_current_user_root(
    session: Session,
) -> None:
    """异常跨账号父边不能影响当前账号的筛选资格、子任务或完整计数。"""

    owner = add_user(session, "cross-child-owner")
    other = add_user(session, "cross-child-other")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    root = add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000001",
        "current-root",
        serial=1,
        created_at=now,
        topic="Tickly",
        status="new",
    )
    other_child = add_task(
        session,
        other.id,
        "00000000-0000-0000-0000-000000000002",
        "cross-user-child",
        serial=1,
        created_at=now + timedelta(minutes=1),
        topic="Work",
        status="completed",
        parent_id=root.id,
    )

    child_only_match = list_tasks(
        session,
        owner.id,
        TaskListQuery(topic="Work", status=TaskStatusFilter.COMPLETED),
    )
    root_match = list_tasks(
        session,
        owner.id,
        TaskListQuery(topic="Tickly", status=TaskStatusFilter.NEW),
    )

    assert child_only_match.items == []
    assert [group.task.id for group in root_match.items] == [root.id]
    assert root_match.items[0].children == []
    assert root_match.items[0].child_count == 0
    assert root_match.items[0].completed_child_count == 0
    assert all(
        child.id != other_child.id
        for group in root_match.items
        for child in group.children
    )


def test_root_group_cursor_uses_root_limit_and_never_splits_children(
    session: Session,
) -> None:
    owner, tasks = add_tree_filter_fixture(session)
    first = list_tasks(
        session,
        owner.id,
        TaskListQuery(sort=TaskSort.SERIAL, order=SortOrder.ASC, limit=1),
    )
    assert first.next_cursor is not None
    second = list_tasks(
        session,
        owner.id,
        TaskListQuery(
            sort=TaskSort.SERIAL,
            order=SortOrder.ASC,
            limit=1,
            cursor=first.next_cursor,
        ),
    )

    assert [group.task.id for group in first.items] == [tasks[1].id]
    assert [child.id for child in first.items[0].children] == [
        tasks[2].id,
        tasks[3].id,
    ]
    assert [group.task.id for group in second.items] == [tasks[4].id]
    assert second.next_cursor is None

    padding = "=" * (-len(first.next_cursor) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode((first.next_cursor + padding).encode("ascii"))
    )
    assert payload["id"] == tasks[1].id
    assert type(payload["value"]) is int
    assert payload["value"] == tasks[1].serial


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        (TaskSort.SERIAL, SortOrder.ASC, ["none", "low", "high"]),
        (TaskSort.SERIAL, SortOrder.DESC, ["high", "low", "none"]),
        (TaskSort.CREATED_AT, SortOrder.ASC, ["none", "low", "high"]),
        (TaskSort.CREATED_AT, SortOrder.DESC, ["high", "low", "none"]),
        (TaskSort.DUE_AT, SortOrder.ASC, ["high", "low", "none"]),
        (TaskSort.DUE_AT, SortOrder.DESC, ["low", "high", "none"]),
        (TaskSort.PRIORITY, SortOrder.ASC, ["low", "high", "none"]),
        (TaskSort.PRIORITY, SortOrder.DESC, ["high", "low", "none"]),
    ],
)
def test_root_sort_modes_page_without_duplicates_or_omissions(
    session: Session,
    sort: TaskSort,
    order: SortOrder,
    expected: list[str],
) -> None:
    owner = add_user(session, f"sort_{sort.value}_{order.value}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000001",
        "none",
        serial=1,
        created_at=now,
        priority=None,
        due_at=None,
    )
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000002",
        "low",
        serial=2,
        created_at=now + timedelta(minutes=1),
        priority="low",
        due_at=now + timedelta(days=2),
    )
    add_task(
        session,
        owner.id,
        "00000000-0000-0000-0000-000000000003",
        "high",
        serial=3,
        created_at=now + timedelta(minutes=2),
        priority="high",
        due_at=now + timedelta(days=1),
    )

    titles: list[str] = []
    cursor: str | None = None
    while True:
        page = list_tasks(
            session,
            owner.id,
            TaskListQuery(
                sort=sort,
                order=order,
                limit=1,
                cursor=cursor,
            ),
        )
        titles.extend(group.task.title for group in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert titles == expected
    assert len(titles) == len(set(titles)) == 3


@pytest.mark.parametrize(
    ("sort", "order", "expected_ids"),
    [
        (
            TaskSort.DUE_AT,
            SortOrder.ASC,
            [
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
                "00000000-0000-0000-0000-000000000004",
            ],
        ),
        (
            TaskSort.DUE_AT,
            SortOrder.DESC,
            [
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000004",
                "00000000-0000-0000-0000-000000000003",
            ],
        ),
        (
            TaskSort.PRIORITY,
            SortOrder.ASC,
            [
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
                "00000000-0000-0000-0000-000000000004",
            ],
        ),
        (
            TaskSort.PRIORITY,
            SortOrder.DESC,
            [
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000004",
                "00000000-0000-0000-0000-000000000003",
            ],
        ),
    ],
)
def test_nullable_root_sorts_continue_through_null_bucket(
    session: Session,
    sort: TaskSort,
    order: SortOrder,
    expected_ids: list[str],
) -> None:
    """可空排序必须用 NULL bucket cursor 稳定遍历同值与空值根任务。"""

    owner = add_user(session, f"null_bucket_{sort.value}_{order.value}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    for index in range(4):
        nullable = index >= 2
        add_task(
            session,
            owner.id,
            f"00000000-0000-0000-0000-{index + 1:012d}",
            f"root-{index + 1}",
            serial=index + 1,
            created_at=now + timedelta(minutes=index),
            due_at=None if nullable else now + timedelta(days=1),
            priority=None if nullable else "medium",
        )

    returned_ids: list[str] = []
    cursor: str | None = None
    used_null_bucket_cursor = False
    while True:
        page = list_tasks(
            session,
            owner.id,
            TaskListQuery(sort=sort, order=order, limit=1, cursor=cursor),
        )
        returned_ids.extend(group.task.id for group in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
        padding = "=" * (-len(cursor) % 4)
        cursor_payload = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        )
        used_null_bucket_cursor |= cursor_payload["null_bucket"] is True

    assert returned_ids == expected_ids
    assert len(returned_ids) == len(set(returned_ids)) == 4
    assert used_null_bucket_cursor is True


@pytest.mark.parametrize("order", [SortOrder.ASC, SortOrder.DESC])
def test_root_cursor_uses_id_as_stable_tie_break(
    session: Session,
    order: SortOrder,
) -> None:
    owner = add_user(session, f"tie_{order.value}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    task_ids = [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
    ]
    for serial, task_id in enumerate(task_ids, start=1):
        add_task(
            session,
            owner.id,
            task_id,
            f"tie-{serial}",
            serial=serial,
            created_at=now,
        )

    returned_ids: list[str] = []
    cursor: str | None = None
    while True:
        page = list_tasks(
            session,
            owner.id,
            TaskListQuery(
                sort=TaskSort.CREATED_AT,
                order=order,
                limit=1,
                cursor=cursor,
            ),
        )
        returned_ids.extend(group.task.id for group in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    expected = task_ids if order is SortOrder.ASC else list(reversed(task_ids))
    assert returned_ids == expected


def _encode_test_cursor_payload(payload: dict[str, object]) -> str:
    """编码经测试定向篡改的游标载荷。"""

    return (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )


def create_serial_cursor_payload(
    session: Session,
) -> tuple[User, dict[str, object]]:
    """生成可定向篡改的合法 serial cursor 载荷。"""

    owner = add_user(session, "serial-cursor-owner")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    for index in range(2):
        add_task(
            session,
            owner.id,
            f"00000000-0000-0000-0000-{index + 1:012d}",
            str(index),
            serial=index + 1,
            created_at=now + timedelta(minutes=index),
            topic="Tickly",
        )
    page = list_tasks(
        session,
        owner.id,
        TaskListQuery(
            topic="Tickly",
            sort=TaskSort.SERIAL,
            order=SortOrder.ASC,
            limit=1,
        ),
    )
    assert page.next_cursor is not None
    padding = "=" * (-len(page.next_cursor) % 4)
    payload: dict[str, object] = json.loads(
        base64.urlsafe_b64decode((page.next_cursor + padding).encode("ascii"))
    )
    return owner, payload


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("v", True),
        ("value", True),
        ("value", 0),
        ("value", -1),
        ("value", "1"),
        ("value", 2**63),
        ("value", 10**100),
    ],
)
def test_serial_cursor_rejects_non_exact_or_out_of_range_integers(
    session: Session,
    field: str,
    invalid_value: object,
) -> None:
    owner, payload = create_serial_cursor_payload(session)
    payload[field] = invalid_value

    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(
                topic="Tickly",
                sort=TaskSort.SERIAL,
                order=SortOrder.ASC,
                cursor=_encode_test_cursor_payload(payload),
            ),
        )


def test_serial_cursor_accepts_sqlite_max_integer(session: Session) -> None:
    owner, payload = create_serial_cursor_payload(session)
    payload["value"] = 2**63 - 1

    page = list_tasks(
        session,
        owner.id,
        TaskListQuery(
            topic="Tickly",
            sort=TaskSort.SERIAL,
            order=SortOrder.ASC,
            cursor=_encode_test_cursor_payload(payload),
        ),
    )

    assert page.items == []
    assert page.next_cursor is None


def test_cursor_rejects_damage_types_versions_and_cross_query_reuse(
    session: Session,
) -> None:
    owner = add_user(session, "cursor-owner")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    for index in range(2):
        add_task(
            session,
            owner.id,
            f"00000000-0000-0000-0000-{index + 1:012d}",
            str(index),
            serial=index + 1,
            created_at=now + timedelta(minutes=index),
            topic="Tickly",
        )
    query = TaskListQuery(
        topic="Tickly",
        sort=TaskSort.SERIAL,
        order=SortOrder.ASC,
        limit=1,
    )
    page = list_tasks(session, owner.id, query)
    assert page.next_cursor is not None
    padding = "=" * (-len(page.next_cursor) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode((page.next_cursor + padding).encode("ascii"))
    )

    unknown_version_payload = {**payload, "v": 2}
    extra_field_payload = {**payload, "unexpected": "value"}
    wrong_serial_type_payload = {**payload, "value": str(payload["value"])}
    wrong_bucket_type_payload = {**payload, "null_bucket": "false"}

    with pytest.raises(InvalidCursor):
        list_tasks(session, owner.id, TaskListQuery(cursor="not-base64"))
    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(cursor=_encode_test_cursor_payload(unknown_version_payload)),
        )
    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(cursor=_encode_test_cursor_payload(extra_field_payload)),
        )
    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(
                topic="Tickly",
                sort=TaskSort.SERIAL,
                order=SortOrder.ASC,
                cursor=_encode_test_cursor_payload(wrong_serial_type_payload),
            ),
        )
    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(
                topic="Tickly",
                sort=TaskSort.SERIAL,
                order=SortOrder.ASC,
                cursor=_encode_test_cursor_payload(wrong_bucket_type_payload),
            ),
        )

    mismatched_queries = [
        TaskListQuery(
            topic="Tickly",
            status=TaskStatusFilter.NEW,
            sort=TaskSort.SERIAL,
            order=SortOrder.ASC,
            cursor=page.next_cursor,
        ),
        TaskListQuery(
            topic="Work",
            sort=TaskSort.SERIAL,
            order=SortOrder.ASC,
            cursor=page.next_cursor,
        ),
        TaskListQuery(
            topic="Tickly",
            sort=TaskSort.CREATED_AT,
            order=SortOrder.ASC,
            cursor=page.next_cursor,
        ),
        TaskListQuery(
            topic="Tickly",
            sort=TaskSort.SERIAL,
            order=SortOrder.DESC,
            cursor=page.next_cursor,
        ),
    ]
    for mismatched_query in mismatched_queries:
        with pytest.raises(InvalidCursor):
            list_tasks(session, owner.id, mismatched_query)

    # limit 不属于查询语义，允许后续页调整。
    continued = list_tasks(
        session,
        owner.id,
        TaskListQuery(
            topic="Tickly",
            sort=TaskSort.SERIAL,
            order=SortOrder.ASC,
            limit=2,
            cursor=page.next_cursor,
        ),
    )
    assert [group.task.serial for group in continued.items] == [2]
    assert continued.next_cursor is None


def test_list_topics_keeps_exact_values_ordered_for_display_and_scoped_to_owner(
    session: Session,
) -> None:
    """主题按精确存储值去重，并只展示当前账号的大小写敏感主题。"""

    owner = add_user(session, "topics-owner")
    other = add_user(session, "topics-other")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    owner_topics = [
        "tickly",
        "Alpha",
        "Tickly",
        "beta",
        "alpha",
        "Tickly",
        "äpfel",
        "Äpfel",
        "ßeta",
        "SSeta",
    ]
    for index, topic in enumerate(owner_topics, start=1):
        add_task(
            session,
            owner.id,
            f"10000000-0000-0000-0000-{index:012d}",
            f"owner-{index}",
            serial=index,
            created_at=now + timedelta(minutes=index),
            topic=topic,
        )
    add_task(
        session,
        other.id,
        "20000000-0000-0000-0000-000000000001",
        "other-topic",
        serial=1,
        created_at=now,
        topic="Aaron",
    )
    empty_owner = add_user(session, "topics-empty")

    assert tasks_service.list_topics(session, owner.id) == [
        "Alpha",
        "alpha",
        "beta",
        "SSeta",
        "ßeta",
        "Tickly",
        "tickly",
        "Äpfel",
        "äpfel",
    ]
    assert tasks_service.list_topics(session, empty_owner.id) == []


def test_parent_options_only_return_current_owner_roots_without_status_or_topic_filter(
    session: Session,
) -> None:
    """父待办候选包含当前账号全部根任务，并隔离子任务与其他账号。"""

    owner = add_user(session, "parent-options-owner")
    other = add_user(session, "parent-options-other")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    later_root = add_task(
        session,
        owner.id,
        "30000000-0000-0000-0000-000000000003",
        "已完成主题根任务",
        serial=3,
        created_at=now,
        topic="Work",
        status="completed",
    )
    first_root = add_task(
        session,
        owner.id,
        "30000000-0000-0000-0000-000000000001",
        "进行中根任务",
        serial=1,
        created_at=now,
        topic="Personal",
        status="in_progress",
    )
    child = add_task(
        session,
        owner.id,
        "30000000-0000-0000-0000-000000000002",
        "不能作为父待办的子任务",
        serial=2,
        created_at=now,
        parent_id=first_root.id,
    )
    other_root = add_task(
        session,
        other.id,
        "40000000-0000-0000-0000-000000000001",
        "其他账号根任务",
        serial=1,
        created_at=now,
    )

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(),
    )

    assert [task.id for task in page.items] == [first_root.id, later_root.id]
    assert page.next_cursor is None
    assert child.id not in {task.id for task in page.items}
    assert other_root.id not in {task.id for task in page.items}


@pytest.mark.parametrize("query", ["18", "#18", "0018", "#0018"])
def test_parent_options_numeric_query_matches_serial_exactly(
    session: Session,
    query: str,
) -> None:
    owner = add_user(session, f"parent-serial-{query.replace('#', 'hash')}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    serial_match = add_task(
        session,
        owner.id,
        "50000000-0000-0000-0000-000000000018",
        "编号命中",
        serial=18,
        created_at=now,
    )
    add_task(
        session,
        owner.id,
        "50000000-0000-0000-0000-000000000019",
        "标题包含 18 但编号不同",
        serial=19,
        created_at=now,
    )

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query=query),
    )

    assert [task.id for task in page.items] == [serial_match.id]


@pytest.mark.parametrize(
    ("query", "matching_title"),
    [
        ("pha", "Alpha project"),
        ("#", "含有 # 的标题"),
        ("-18", "处理 -18 边界"),
        ("18x", "混合 18x 字符"),
    ],
)
def test_parent_options_non_numeric_query_uses_normalized_title_contains(
    session: Session,
    query: str,
    matching_title: str,
) -> None:
    owner = add_user(session, f"parent-title-{len(query)}-{ord(query[0])}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    match = add_task(
        session,
        owner.id,
        "60000000-0000-0000-0000-000000000001",
        matching_title,
        serial=1,
        created_at=now,
    )
    add_task(
        session,
        owner.id,
        "60000000-0000-0000-0000-000000000002",
        "完全无关",
        serial=2,
        created_at=now,
    )

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query=f"  {query}  "),
    )

    assert [task.id for task in page.items] == [match.id]


@pytest.mark.parametrize(
    ("query", "matching_title", "non_matching_title"),
    [
        ("%", "完成度 100%", "没有百分号"),
        ("_", "snake_case", "snakeXcase"),
        ("/", "path/to", "path-to"),
        ("alpha", "ALPHA Project", "Beta Project"),
        ("中文", "包含中文的标题", "不相关标题"),
    ],
)
def test_parent_options_title_query_is_literal_ascii_case_insensitive_contains(
    session: Session,
    query: str,
    matching_title: str,
    non_matching_title: str,
) -> None:
    """标题搜索转义 LIKE 元字符，仅对 ASCII 大小写保持不敏感体验。"""

    owner = add_user(session, f"parent-literal-{ord(query[0])}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    match = add_task(
        session,
        owner.id,
        "61000000-0000-0000-0000-000000000001",
        matching_title,
        serial=1,
        created_at=now,
    )
    add_task(
        session,
        owner.id,
        "61000000-0000-0000-0000-000000000002",
        non_matching_title,
        serial=2,
        created_at=now,
    )

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query=query),
    )

    assert [task.id for task in page.items] == [match.id]


def test_parent_options_title_search_still_excludes_child_and_other_user_root(
    session: Session,
) -> None:
    """搜索条件命中时仍必须先执行当前账号和根任务隔离。"""

    owner = add_user(session, "parent-search-owner")
    other = add_user(session, "parent-search-other")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    root = add_task(
        session,
        owner.id,
        "62000000-0000-0000-0000-000000000001",
        "Shared Needle Root",
        serial=1,
        created_at=now,
    )
    child = add_task(
        session,
        owner.id,
        "62000000-0000-0000-0000-000000000002",
        "Shared Needle Child",
        serial=2,
        created_at=now,
        parent_id=root.id,
    )
    other_root = add_task(
        session,
        other.id,
        "63000000-0000-0000-0000-000000000001",
        "Shared Needle Other",
        serial=1,
        created_at=now,
    )

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query="needle"),
    )

    assert [task.id for task in page.items] == [root.id]
    assert child.id not in {task.id for task in page.items}
    assert other_root.id not in {task.id for task in page.items}


@pytest.mark.parametrize("query", ["#１２", "#١٨", "#²", "##18"])
def test_parent_options_only_parse_ascii_digits_as_serial(
    session: Session,
    query: str,
) -> None:
    """非 ASCII 数字与重复井号属于普通标题文本，不得改写为 serial。"""

    owner = add_user(session, f"parent-ascii-serial-{ord(query[-1])}-{len(query)}")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    add_task(
        session,
        owner.id,
        "64000000-0000-0000-0000-000000000018",
        "仅编号为十八",
        serial=18,
        created_at=now,
    )
    title_match = add_task(
        session,
        owner.id,
        "64000000-0000-0000-0000-000000000019",
        f"标题字面量 {query}",
        serial=19,
        created_at=now,
    )

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query=query),
    )

    assert [task.id for task in page.items] == [title_match.id]


def test_parent_options_limit_one_pages_without_duplicates_and_binds_query_cursor(
    session: Session,
) -> None:
    """候选 cursor 以 serial、id 定位，并绑定生成它的规范化搜索词。"""

    owner = add_user(session, "parent-page-owner")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    expected_ids = []
    for index in range(1, 4):
        task = add_task(
            session,
            owner.id,
            f"70000000-0000-0000-0000-{index:012d}",
            f"Project {index}",
            serial=index,
            created_at=now + timedelta(minutes=index),
        )
        expected_ids.append(task.id)

    first = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query="  Project  ", limit=1),
    )
    assert first.next_cursor is not None
    with pytest.raises(InvalidCursor):
        tasks_service.list_parent_options(
            session,
            owner.id,
            ParentOptionQuery(query="project-else", cursor=first.next_cursor),
        )

    returned_ids = [first.items[0].id]
    cursor = first.next_cursor
    while cursor is not None:
        page = tasks_service.list_parent_options(
            session,
            owner.id,
            ParentOptionQuery(query="Project", limit=1, cursor=cursor),
        )
        returned_ids.extend(task.id for task in page.items)
        cursor = page.next_cursor

    assert returned_ids == expected_ids
    assert len(returned_ids) == len(set(returned_ids)) == 3


def test_parent_options_cursor_continues_after_anchor_is_deleted(
    session: Session,
) -> None:
    """keyset cursor 不依赖锚点仍存在，删除上一页末项后必须继续向后读取。"""

    owner = add_user(session, "parent-deleted-anchor-owner")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    tasks = [
        add_task(
            session,
            owner.id,
            f"71000000-0000-0000-0000-{index:012d}",
            f"anchor {index}",
            serial=index,
            created_at=now + timedelta(minutes=index),
        )
        for index in range(1, 4)
    ]
    first = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(limit=1),
    )
    assert first.next_cursor is not None
    session.delete(tasks[0])
    session.commit()

    returned_ids: list[str] = []
    cursor = first.next_cursor
    while cursor is not None:
        page = tasks_service.list_parent_options(
            session,
            owner.id,
            ParentOptionQuery(limit=1, cursor=cursor),
        )
        returned_ids.extend(task.id for task in page.items)
        cursor = page.next_cursor

    assert returned_ids == [tasks[1].id, tasks[2].id]


def test_parent_options_cursor_allows_limit_change_without_duplicates_or_omissions(
    session: Session,
) -> None:
    """limit 不属于搜索语义，后续页调整大小仍按同一 keyset 连续遍历。"""

    owner = add_user(session, "parent-limit-change-owner")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    expected_ids = []
    for index in range(1, 5):
        task = add_task(
            session,
            owner.id,
            f"72000000-0000-0000-0000-{index:012d}",
            f"limit {index}",
            serial=index,
            created_at=now + timedelta(minutes=index),
        )
        expected_ids.append(task.id)

    first = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(limit=1),
    )
    assert first.next_cursor is not None
    second = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(limit=2, cursor=first.next_cursor),
    )
    assert second.next_cursor is not None
    third = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(limit=3, cursor=second.next_cursor),
    )

    returned_ids = [
        task.id
        for page in (first, second, third)
        for task in page.items
    ]
    assert returned_ids == expected_ids
    assert len(returned_ids) == len(set(returned_ids)) == 4
    assert third.next_cursor is None


def test_parent_cursor_is_independent_and_rejects_malformed_payloads(
    session: Session,
) -> None:
    """父候选 cursor 必须严格拒绝损坏、宽松类型和越界 SQLite 整数。"""

    owner = add_user(session, "parent-cursor-owner")
    now = datetime(2026, 8, 18, 8, tzinfo=UTC)
    for index in range(1, 3):
        add_task(
            session,
            owner.id,
            f"80000000-0000-0000-0000-{index:012d}",
            f"cursor {index}",
            serial=index,
            created_at=now + timedelta(minutes=index),
        )
    query = ParentOptionQuery(query="cursor", limit=1)
    page = tasks_service.list_parent_options(session, owner.id, query)
    assert page.next_cursor is not None
    padding = "=" * (-len(page.next_cursor) % 4)
    payload: dict[str, object] = json.loads(
        base64.urlsafe_b64decode((page.next_cursor + padding).encode("ascii"))
    )
    assert set(payload) == {"v", "query", "serial", "id"}

    invalid_payloads = [
        {**payload, "v": True},
        {**payload, "v": 2},
        {**payload, "unexpected": "value"},
        {**payload, "query": 1},
        {**payload, "serial": True},
        {**payload, "serial": 0},
        {**payload, "serial": -1},
        {**payload, "serial": "1"},
        {**payload, "serial": 2**63},
        {**payload, "serial": 10**100},
        {**payload, "id": False},
        {**payload, "id": "not-a-uuid"},
    ]
    invalid_cursors = [
        "not-base64",
        base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode("ascii"),
        *[_encode_test_cursor_payload(value) for value in invalid_payloads],
    ]
    for invalid_cursor in invalid_cursors:
        with pytest.raises(InvalidCursor):
            tasks_service.list_parent_options(
                session,
                owner.id,
                ParentOptionQuery(query="cursor", cursor=invalid_cursor),
            )

    task_page = list_tasks(
        session,
        owner.id,
        TaskListQuery(sort=TaskSort.SERIAL, order=SortOrder.ASC, limit=1),
    )
    assert task_page.next_cursor is not None
    with pytest.raises(InvalidCursor):
        tasks_service.list_parent_options(
            session,
            owner.id,
            ParentOptionQuery(cursor=task_page.next_cursor),
        )
    with pytest.raises(InvalidCursor):
        list_tasks(
            session,
            owner.id,
            TaskListQuery(cursor=page.next_cursor),
        )


def test_parent_options_huge_numeric_query_returns_empty_without_overflow(
    session: Session,
) -> None:
    """超过 SQLite INTEGER 的纯数字搜索仍是精确编号查询，但不得泄漏溢出异常。"""

    owner = add_user(session, "parent-huge-query-owner")

    page = tasks_service.list_parent_options(
        session,
        owner.id,
        ParentOptionQuery(query=str(2**63)),
    )

    assert page.items == []
    assert page.next_cursor is None
