import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.mcp_dependencies import get_mcp_current_user
from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings, create_session_factory
from app.main import create_app
from app.models import Task, User
from app.services.accounts import create_account


RAW_TOKEN = "test-mcp-token"
TOKEN_HASH = hashlib.sha256(RAW_TOKEN.encode("utf-8")).hexdigest()


@pytest.fixture
def mcp_client(tmp_path: Path) -> Iterator[TestClient]:
    """创建启用 MCP Token 且只有一个账号的真实 HTTP 测试应用。"""

    database_url = f"sqlite:///{tmp_path / 'mcp-tasks-api.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    settings = Settings(
        environment=Environment.TEST,
        database_url=database_url,
        jwt_secret="s" * 64,
        mcp_token_sha256=TOKEN_HASH,
        _env_file=None,
    )
    engine = create_engine_for_settings(settings)
    with create_session_factory(engine)() as session:
        create_account(session, "potato", "correct horse battery staple")
    app = create_app(settings, database_engine=engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    engine.dispose()


@pytest.fixture
def mcp_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_TOKEN}"}


def add_task(
    client: TestClient,
    *,
    serial: int,
    title: str,
    topic: str = "工作",
    status: str = "new",
    parent_id: str | None = None,
    user_id: str | None = None,
    priority: str | None = None,
    due_at: datetime | None = None,
) -> Task:
    """直接写入边界数据，HTTP 断言仍通过真实路由和 service 执行。"""

    with client.app.state.database_session_factory() as session:
        resolved_user_id = user_id or session.scalar(select(User.id))
        assert resolved_user_id is not None
        owner = session.get(User, resolved_user_id)
        assert owner is not None
        task = Task(
            user_id=resolved_user_id,
            serial=serial,
            title=title,
            description=title,
            topic=topic,
            status=status,
            parent_id=parent_id,
            priority=priority,
            due_at=due_at,
        )
        # 直接构造的测试数据也必须维护账号计数器，才能真实验证后续 HTTP 创建语义。
        owner.next_task_serial = max(owner.next_task_serial, serial + 1)
        session.add(task)
        session.commit()
        return task


@pytest.fixture
def owned_task(mcp_client: TestClient) -> Task:
    return add_task(mcp_client, serial=1, title="账号内任务")


@pytest.mark.parametrize(
    "path",
    [
        "/internal/mcp/v1/tasks",
        "/internal/mcp/v1/tasks/topics",
        "/internal/mcp/v1/tasks/parent-options",
        "/internal/mcp/v1/tasks/1",
    ],
)
def test_internal_routes_require_mcp_token(
    mcp_client: TestClient,
    path: str,
) -> None:
    response = mcp_client.get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_mcp_token_cannot_access_public_task_api(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    response = mcp_client.get("/api/v1/tasks", headers=mcp_headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_internal_detail_resolves_owned_task_by_serial(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    owned_task: Task,
) -> None:
    child = add_task(
        mcp_client,
        serial=2,
        title="直接子任务",
        parent_id=owned_task.id,
    )

    response = mcp_client.get(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
    )

    assert response.status_code == 200
    assert response.json()["serial"] == owned_task.serial
    assert [item["serial"] for item in response.json()["children"]] == [child.serial]


def test_internal_list_keeps_complete_root_groups_and_cursor(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    first_root = add_task(mcp_client, serial=1, title="根一")
    child = add_task(
        mcp_client,
        serial=2,
        title="根一子项",
        parent_id=first_root.id,
    )
    second_root = add_task(mcp_client, serial=3, title="根二")

    first = mcp_client.get(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        params={"sort": "serial", "order": "asc", "limit": 1},
    )
    second = mcp_client.get(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        params={
            "sort": "serial",
            "order": "asc",
            "limit": 1,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["items"][0]["task"]["serial"] == first_root.serial
    assert first.json()["items"][0]["children"][0]["serial"] == child.serial
    assert second.json()["items"][0]["task"]["serial"] == second_root.serial
    assert second.json()["next_cursor"] is None


def test_internal_topics_and_parent_options_keep_owned_static_route_semantics(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    first_root = add_task(
        mcp_client,
        serial=1,
        title="Alpha 根",
        topic="Tickly",
    )
    second_root = add_task(mcp_client, serial=2, title="Beta 根", topic="工作")
    child = add_task(
        mcp_client,
        serial=3,
        title="Alpha 子项",
        topic="子主题",
        parent_id=first_root.id,
    )

    topics = mcp_client.get(
        "/internal/mcp/v1/tasks/topics",
        headers=mcp_headers,
    )
    first_page = mcp_client.get(
        "/internal/mcp/v1/tasks/parent-options",
        headers=mcp_headers,
        params={"limit": 1},
    )
    second_page = mcp_client.get(
        "/internal/mcp/v1/tasks/parent-options",
        headers=mcp_headers,
        params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
    )
    searched = mcp_client.get(
        "/internal/mcp/v1/tasks/parent-options",
        headers=mcp_headers,
        params={"query": "Alpha"},
    )

    assert topics.status_code == 200
    expected_topics = sorted(
        ["Tickly", "工作", "子主题"],
        key=lambda value: (value.casefold(), value),
    )
    assert topics.json() == {"items": expected_topics}
    assert (
        first_page.status_code
        == second_page.status_code
        == searched.status_code
        == 200
    )
    assert [
        first_page.json()["items"][0]["serial"],
        second_page.json()["items"][0]["serial"],
    ] == [first_root.serial, second_root.serial]
    assert [item["serial"] for item in searched.json()["items"]] == [
        first_root.serial
    ]
    assert child.serial not in {
        item["serial"]
        for response in (first_page, second_page, searched)
        for item in response.json()["items"]
    }


def test_internal_detail_does_not_leak_another_accounts_same_serial(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    owned = add_task(mcp_client, serial=1, title="账号内任务")
    with mcp_client.app.state.database_session_factory() as session:
        owner = session.get(User, owned.user_id)
        assert owner is not None
        other = User(username="other", password_hash="test-hash")
        session.add(other)
        session.commit()
        owner_id = owner.id
        other_id = other.id
    add_task(
        mcp_client,
        serial=1,
        title="其他账号同号任务",
        user_id=other_id,
    )

    def resolve_owner() -> User:
        with mcp_client.app.state.database_session_factory() as session:
            resolved = session.get(User, owner_id)
            assert resolved is not None
            session.expunge(resolved)
            return resolved

    mcp_client.app.dependency_overrides[get_mcp_current_user] = resolve_owner
    try:
        owned_response = mcp_client.get(
            "/internal/mcp/v1/tasks/1",
            headers=mcp_headers,
        )
        missing_response = mcp_client.get(
            "/internal/mcp/v1/tasks/999",
            headers=mcp_headers,
        )
    finally:
        mcp_client.app.dependency_overrides.clear()

    assert owned_response.status_code == 200
    assert owned_response.json()["title"] == "账号内任务"
    assert "其他账号同号任务" not in owned_response.text
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "task_not_found"


@pytest.mark.parametrize(
    "path",
    [
        "/internal/mcp/v1/tasks",
        "/internal/mcp/v1/tasks/parent-options",
    ],
)
def test_internal_invalid_cursors_use_stable_error(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    path: str,
) -> None:
    response = mcp_client.get(
        path,
        headers=mcp_headers,
        params={"cursor": "not-base64"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"
    assert "not-base64" not in response.text


def test_internal_serial_rejects_values_outside_sqlite_integer_range(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    response = mcp_client.get(
        "/internal/mcp/v1/tasks/9223372036854775808",
        headers=mcp_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_internal_routes_are_absent_from_public_openapi(
    mcp_client: TestClient,
) -> None:
    paths = mcp_client.get("/openapi.json").json()["paths"]

    assert not any(path.startswith("/internal/mcp/") for path in paths)


def test_internal_create_resolves_parent_serial_in_same_account(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    root = add_task(mcp_client, serial=1, title="父任务")

    response = mcp_client.post(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        json={"title": "子任务", "topic": "工作", "parent_serial": root.serial},
    )

    assert response.status_code == 201
    assert response.json()["serial"] == 2
    assert response.json()["parent_id"] == root.id
    assert response.json()["description"] == "子任务"


def test_internal_create_rejects_child_parent_without_consuming_serial(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    root = add_task(mcp_client, serial=1, title="根任务")
    child = add_task(
        mcp_client,
        serial=2,
        title="现有子任务",
        parent_id=root.id,
    )

    rejected = mcp_client.post(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        json={"title": "二层任务", "topic": "工作", "parent_serial": child.serial},
    )
    recovered = mcp_client.post(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        json={"title": "回滚后任务", "topic": "工作"},
    )

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_task_relationship"
    assert recovered.status_code == 201
    assert recovered.json()["serial"] == 3


def test_internal_create_rejects_cross_account_parent_serial(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    with mcp_client.app.state.database_session_factory() as session:
        owner = session.scalar(select(User))
        assert owner is not None
        other = User(username="other-parent", password_hash="test-hash")
        session.add(other)
        session.commit()
        owner_id = owner.id
        other_id = other.id
    add_task(
        mcp_client,
        serial=1,
        title="其他账号父任务",
        user_id=other_id,
    )

    def resolve_owner() -> User:
        with mcp_client.app.state.database_session_factory() as session:
            resolved = session.get(User, owner_id)
            assert resolved is not None
            session.expunge(resolved)
            return resolved

    mcp_client.app.dependency_overrides[get_mcp_current_user] = resolve_owner
    try:
        response = mcp_client.post(
            "/internal/mcp/v1/tasks",
            headers=mcp_headers,
            json={"title": "越权子任务", "topic": "工作", "parent_serial": 1},
        )
    finally:
        mcp_client.app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_task_relationship"
    assert "其他账号父任务" not in response.text


def test_internal_patch_distinguishes_omitted_and_null(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    root = add_task(mcp_client, serial=1, title="父任务")
    child = add_task(
        mcp_client,
        serial=2,
        title="保留标题",
        parent_id=root.id,
        priority="high",
        due_at=datetime(2026, 8, 20, 8, tzinfo=UTC),
    )

    response = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{child.serial}",
        headers=mcp_headers,
        json={"priority": None, "due_at": None, "parent_serial": None},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "保留标题"
    assert response.json()["priority"] is None
    assert response.json()["due_at"] is None
    assert response.json()["parent_id"] is None


def test_internal_status_uses_existing_completion_semantics(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    owned_task: Task,
) -> None:
    response = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
        json={"status": "completed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["completed_at"].endswith("Z")


@pytest.mark.parametrize("field", ["title", "description", "topic", "status"])
def test_internal_patch_rejects_null_for_required_fields(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    owned_task: Task,
    field: str,
) -> None:
    response = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
        json={field: None},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_internal_patch_rejects_self_parent_and_rolls_back_other_fields(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    owned_task: Task,
) -> None:
    rejected = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
        json={"title": "不应保留", "parent_serial": owned_task.serial},
    )
    persisted = mcp_client.get(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
    )

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_task_relationship"
    assert persisted.status_code == 200
    assert persisted.json()["title"] == "账号内任务"


@pytest.mark.parametrize("invalid_parent_serial", [True, 1.0, "1"])
def test_internal_create_requires_parent_serial_to_be_a_strict_integer(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    invalid_parent_serial: object,
) -> None:
    response = mcp_client.post(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        json={
            "title": "严格父流水号",
            "topic": "工作",
            "parent_serial": invalid_parent_serial,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("invalid_parent_serial", [True, 1.0, "1"])
def test_internal_update_requires_parent_serial_to_be_a_strict_integer(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    owned_task: Task,
    invalid_parent_serial: object,
) -> None:
    response = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
        json={"parent_serial": invalid_parent_serial},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_internal_contract_has_no_delete(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    owned_task: Task,
) -> None:
    response = mcp_client.delete(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
    )

    assert response.status_code == 405
