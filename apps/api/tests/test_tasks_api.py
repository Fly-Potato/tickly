from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings, create_session_factory
from app.main import create_app
from app.models import Task, User
from app.services.accounts import create_account


PASSWORD = "correct horse battery staple"
TASK_RESPONSE_FIELDS = {
    "id",
    "serial",
    "title",
    "description",
    "priority",
    "topic",
    "status",
    "due_at",
    "completed_at",
    "parent_id",
    "created_at",
    "updated_at",
}


@pytest.fixture
def task_client(tmp_path: Path) -> Iterator[TestClient]:
    database_path = tmp_path / "tasks-api.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    settings = Settings(
        environment=Environment.TEST,
        database_url=database_url,
        jwt_secret="s" * 64,
        _env_file=None,
    )
    engine = create_engine_for_settings(settings)
    with create_session_factory(engine)() as session:
        create_account(session, "potato", PASSWORD)
    app = create_app(settings, database_engine=engine)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def auth_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "potato", "password": PASSWORD},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def create_task_via_api(
    client: TestClient,
    headers: dict[str, str],
    title: str,
    *,
    topic: str = "默认",
    **fields: object,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": title, "topic": topic, **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_foreign_task(
    client: TestClient,
    *,
    title: str = "其他用户任务",
    topic: str = "其他主题",
    parent_id: str | None = None,
    serial: int = 1,
) -> str:
    """直接构造另一账号数据，只用于验证 HTTP 所有权边界。"""

    with client.app.state.database_session_factory() as session:
        other = session.scalar(select(User).where(User.username == "other"))
        if other is None:
            other = User(username="other", password_hash="test-hash")
            session.add(other)
            session.flush()
        task = Task(
            user_id=other.id,
            serial=serial,
            title=title,
            description=title,
            topic=topic,
            status="new",
            parent_id=parent_id,
        )
        session.add(task)
        session.commit()
        return task.id


@pytest.mark.parametrize("description", [None, "", "   "])
def test_create_uses_new_contract_and_defaults_description(
    task_client: TestClient,
    description: str | None,
) -> None:
    headers = auth_headers(task_client)
    payload: dict[str, object] = {
        "title": "  第一项任务  ",
        "topic": "  Tickly  ",
        "priority": "high",
        "due_at": "2026-07-30T18:00:00+08:00",
    }
    if description is not None:
        payload["description"] = description

    response = task_client.post("/api/v1/tasks", headers=headers, json=payload)
    body = response.json()

    assert response.status_code == 201
    assert set(body) == TASK_RESPONSE_FIELDS
    assert body["serial"] == 1
    assert body["title"] == "第一项任务"
    assert body["description"] == "第一项任务"
    assert body["topic"] == "Tickly"
    assert body["priority"] == "high"
    assert body["status"] == "new"
    assert body["due_at"] == "2026-07-30T10:00:00Z"
    assert body["completed_at"] is None
    assert body["parent_id"] is None


def test_patch_maintains_completion_time_and_independent_description(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)
    created = create_task_via_api(
        task_client,
        headers,
        "原标题",
        topic="工作",
        description="独立描述",
        priority="low",
        due_at="2026-08-18T10:00:00Z",
    )

    completed = task_client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=headers,
        json={"status": "completed"},
    )
    title_only = task_client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=headers,
        json={"title": "新标题"},
    )
    reopened = task_client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=headers,
        json={
            "status": "in_progress",
            "priority": None,
            "due_at": None,
            "parent_id": None,
        },
    )
    completed_again = task_client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=headers,
        json={"status": "completed"},
    )
    reset_to_new = task_client.patch(
        f"/api/v1/tasks/{created['id']}",
        headers=headers,
        json={"status": "new"},
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"].endswith("Z")
    assert title_only.status_code == 200
    assert title_only.json()["description"] == "独立描述"
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "in_progress"
    assert reopened.json()["completed_at"] is None
    assert reopened.json()["priority"] is None
    assert reopened.json()["due_at"] is None
    assert reopened.json()["parent_id"] is None
    assert completed_again.json()["completed_at"].endswith("Z")
    assert reset_to_new.json()["status"] == "new"
    assert reset_to_new.json()["completed_at"] is None


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/tasks", {"title": "任务", "topic": "工作"}),
        ("get", "/api/v1/tasks", None),
        ("get", "/api/v1/tasks/topics", None),
        ("get", "/api/v1/tasks/parent-options", None),
        ("get", "/api/v1/tasks/missing", None),
        ("patch", "/api/v1/tasks/missing", {"title": "任务"}),
        ("delete", "/api/v1/tasks/missing", None),
    ],
)
def test_all_task_operations_require_authentication(
    task_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = task_client.request(method, path, json=payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_foreign_and_missing_task_share_the_same_404(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    foreign_id = add_foreign_task(task_client)

    foreign_responses = [
        task_client.get(f"/api/v1/tasks/{foreign_id}", headers=headers),
        task_client.patch(
            f"/api/v1/tasks/{foreign_id}",
            headers=headers,
            json={"title": "不能修改"},
        ),
        task_client.delete(f"/api/v1/tasks/{foreign_id}", headers=headers),
    ]
    missing = task_client.get(
        "/api/v1/tasks/00000000-0000-0000-0000-999999999999",
        headers=headers,
    )

    for foreign in foreign_responses:
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"]["code"] == "task_not_found"
        assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "任务"},
        {"title": "   ", "topic": "工作"},
        {"title": "任务", "topic": None},
        {"title": "任务", "topic": "   "},
        {"title": "任务", "topic": "工作", "status": "new"},
        {"title": "任务", "topic": "工作", "serial": 9},
        {"title": "任务", "topic": "工作", "completed_at": "2026-08-18T10:00:00Z"},
        {"title": "任务", "topic": "工作", "user_id": "secret"},
        {"title": "任务", "topic": "工作", "next_task_serial": 10},
        {"title": "任务", "topic": "工作", "due_at": "2026-08-18T10:00:00"},
    ],
)
def test_create_rejects_missing_required_and_server_managed_fields(
    task_client: TestClient,
    payload: dict[str, object],
) -> None:
    response = task_client.post(
        "/api/v1/tasks", headers=auth_headers(task_client), json=payload
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": None},
        {"title": "   "},
        {"description": None},
        {"description": "   "},
        {"topic": None},
        {"topic": "   "},
        {"status": None},
        {"serial": 2},
        {"completed_at": "2026-08-18T10:00:00Z"},
        {"user_id": "secret"},
        {"next_task_serial": 10},
    ],
)
def test_patch_rejects_empty_required_and_server_managed_fields(
    task_client: TestClient,
    payload: dict[str, object],
) -> None:
    headers = auth_headers(task_client)
    created = create_task_via_api(task_client, headers, "任务", topic="工作")

    response = task_client.patch(
        f"/api/v1/tasks/{created['id']}", headers=headers, json=payload
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "secret" not in response.text


def test_list_returns_root_groups_and_child_filter_context(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    root = create_task_via_api(task_client, headers, "父任务", topic="父主题")
    matched = create_task_via_api(
        task_client,
        headers,
        "命中子任务",
        topic="筛选主题",
        parent_id=root["id"],
    )
    create_task_via_api(
        task_client,
        headers,
        "未命中子任务",
        topic="其他主题",
        parent_id=root["id"],
    )
    task_client.patch(
        f"/api/v1/tasks/{matched['id']}",
        headers=headers,
        json={"status": "completed"},
    )
    add_foreign_task(task_client, parent_id=str(root["id"]), serial=1)

    response = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={
            "status": "completed",
            "topic": "筛选主题",
            "sort": "serial",
            "order": "asc",
        },
    )
    group = response.json()["items"][0]

    assert response.status_code == 200
    assert group["task"]["id"] == root["id"]
    assert [child["id"] for child in group["children"]] == [matched["id"]]
    assert group["child_count"] == 2
    assert group["completed_child_count"] == 1
    assert group["context_only"] is True
    assert "其他用户任务" not in response.text


def test_list_cursor_pages_complete_root_groups(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    first_root = create_task_via_api(task_client, headers, "根一", topic="工作")
    first_child = create_task_via_api(
        task_client,
        headers,
        "根一子项",
        topic="工作",
        parent_id=first_root["id"],
    )
    second_root = create_task_via_api(task_client, headers, "根二", topic="工作")

    first = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={"sort": "serial", "order": "asc", "limit": 1},
    )
    second = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={
            "sort": "serial",
            "order": "asc",
            "limit": 1,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["items"][0]["task"]["id"] == first_root["id"]
    assert first.json()["items"][0]["children"][0]["id"] == first_child["id"]
    assert second.json()["items"][0]["task"]["id"] == second_root["id"]
    assert second.json()["next_cursor"] is None


def test_topics_and_parent_options_are_static_owned_routes(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)
    root_one = create_task_via_api(task_client, headers, "Alpha 根", topic="Tickly")
    root_two = create_task_via_api(task_client, headers, "Beta 根", topic="工作")
    child = create_task_via_api(
        task_client,
        headers,
        "Alpha 子",
        topic="子主题",
        parent_id=root_one["id"],
    )
    add_foreign_task(task_client, title="Alpha 外部", topic="外部主题")

    topics = task_client.get("/api/v1/tasks/topics", headers=headers)
    first_page = task_client.get(
        "/api/v1/tasks/parent-options",
        headers=headers,
        params={"limit": 1},
    )
    second_page = task_client.get(
        "/api/v1/tasks/parent-options",
        headers=headers,
        params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
    )
    searched = task_client.get(
        "/api/v1/tasks/parent-options",
        headers=headers,
        params={"query": "Alpha"},
    )

    assert topics.status_code == 200
    expected_topics = sorted(
        ["Tickly", "工作", "子主题"],
        key=lambda value: (value.casefold(), value),
    )
    assert topics.json() == {"items": expected_topics}
    assert first_page.status_code == second_page.status_code == searched.status_code == 200
    assert [first_page.json()["items"][0]["id"], second_page.json()["items"][0]["id"]] == [
        root_one["id"],
        root_two["id"],
    ]
    assert searched.json()["items"][0]["id"] == root_one["id"]
    assert child["id"] not in {
        item["id"]
        for response in (first_page, second_page, searched)
        for item in response.json()["items"]
    }
    assert "外部主题" not in topics.text
    assert "Alpha 外部" not in first_page.text + second_page.text + searched.text


def test_list_and_parent_option_invalid_cursors_use_stable_error(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)

    responses = [
        task_client.get(
            "/api/v1/tasks", headers=headers, params={"cursor": "not-base64"}
        ),
        task_client.get(
            "/api/v1/tasks/parent-options",
            headers=headers,
            params={"cursor": "not-base64"},
        ),
    ]

    for response in responses:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cursor"
        assert response.json()["error"]["message"] == "分页游标无效"
        assert "not-base64" not in response.text


def test_detail_returns_only_owned_direct_children(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    root = create_task_via_api(task_client, headers, "父任务", topic="工作")
    child = create_task_via_api(
        task_client,
        headers,
        "子任务",
        topic="工作",
        parent_id=root["id"],
    )
    add_foreign_task(task_client, parent_id=str(root["id"]), serial=1)

    root_detail = task_client.get(f"/api/v1/tasks/{root['id']}", headers=headers)
    child_detail = task_client.get(f"/api/v1/tasks/{child['id']}", headers=headers)

    assert root_detail.status_code == child_detail.status_code == 200
    assert [item["id"] for item in root_detail.json()["children"]] == [child["id"]]
    assert child_detail.json()["children"] == []
    assert "其他用户任务" not in root_detail.text


def test_invalid_parent_relationships_share_422_and_do_not_consume_serial(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)
    root = create_task_via_api(task_client, headers, "根任务", topic="工作")
    foreign_id = add_foreign_task(task_client)

    cross_user = task_client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "跨用户子项", "topic": "工作", "parent_id": foreign_id},
    )
    child = create_task_via_api(
        task_client,
        headers,
        "合法子项",
        topic="工作",
        parent_id=root["id"],
    )
    second_level = task_client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "二层子项", "topic": "工作", "parent_id": child["id"]},
    )
    next_root = create_task_via_api(task_client, headers, "下一根任务", topic="工作")

    for response in (cross_user, second_level):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_task_relationship"
        assert response.json()["error"]["message"] == "父待办关系无效"
        assert foreign_id not in response.text
        assert child["id"] not in response.text
    assert child["serial"] == 2
    assert next_root["serial"] == 3


def test_update_rejects_making_a_parent_into_a_child(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    first_root = create_task_via_api(task_client, headers, "父任务", topic="工作")
    create_task_via_api(
        task_client,
        headers,
        "子任务",
        topic="工作",
        parent_id=first_root["id"],
    )
    second_root = create_task_via_api(task_client, headers, "另一根任务", topic="工作")

    response = task_client.patch(
        f"/api/v1/tasks/{first_root['id']}",
        headers=headers,
        json={"parent_id": second_root["id"]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_task_relationship"
    assert response.json()["error"]["message"] == "父待办关系无效"


def test_delete_parent_promotes_child_without_status_cascade(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)
    parent = create_task_via_api(task_client, headers, "父任务", topic="工作")
    child = create_task_via_api(
        task_client,
        headers,
        "子任务",
        topic="工作",
        parent_id=parent["id"],
    )
    task_client.patch(
        f"/api/v1/tasks/{parent['id']}",
        headers=headers,
        json={"status": "completed"},
    )
    task_client.patch(
        f"/api/v1/tasks/{child['id']}",
        headers=headers,
        json={"status": "in_progress"},
    )

    deleted = task_client.delete(f"/api/v1/tasks/{parent['id']}", headers=headers)
    promoted = task_client.get(f"/api/v1/tasks/{child['id']}", headers=headers)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert promoted.status_code == 200
    assert promoted.json()["parent_id"] is None
    assert promoted.json()["status"] == "in_progress"
    assert promoted.json()["completed_at"] is None


def test_openapi_exposes_new_owned_contract_without_server_fields(
    task_client: TestClient,
) -> None:
    document = task_client.get("/openapi.json").json()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    assert set(paths["/api/v1/tasks"]) == {"get", "post"}
    assert set(paths["/api/v1/tasks/topics"]) == {"get"}
    assert set(paths["/api/v1/tasks/parent-options"]) == {"get"}
    assert set(paths["/api/v1/tasks/{task_id}"]) == {"get", "patch", "delete"}
    assert paths["/api/v1/tasks/{task_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/TaskDetailResponse")
    assert paths["/api/v1/tasks"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/TaskListResponse")

    request_server_fields = {"serial", "completed_at", "user_id", "next_task_serial"}
    assert request_server_fields.isdisjoint(schemas["TaskCreateRequest"]["properties"])
    assert request_server_fields.isdisjoint(schemas["TaskUpdateRequest"]["properties"])
    assert "user_id" not in schemas["TaskResponse"]["properties"]
    assert "next_task_serial" not in schemas["TaskResponse"]["properties"]
    assert schemas["TaskListResponse"]["properties"]["items"]["items"]["$ref"].endswith(
        "/TaskGroupResponse"
    )
    assert schemas["TaskDetailResponse"]["properties"]["children"]["items"][
        "$ref"
    ].endswith("/TaskResponse")
