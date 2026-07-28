from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings, create_session_factory
from app.main import create_app
from app.models import Task, User
from app.services.accounts import create_account


PASSWORD = "correct horse battery staple"


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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def create_task_via_api(
    client: TestClient, headers: dict[str, str], title: str, **fields: object
) -> dict[str, object]:
    response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": title, **fields},
    )
    assert response.status_code == 201
    return response.json()


def test_task_crud_and_completion_contract(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    created = task_client.post(
        "/api/v1/tasks",
        headers=headers,
        json={
            "title": "  第一项任务  ",
            "priority": "high",
            "due_at": "2026-07-30T18:00:00+08:00",
        },
    )
    task_id = created.json()["id"]

    assert created.status_code == 201
    assert created.json()["title"] == "第一项任务"
    assert created.json()["due_at"].endswith("Z")
    assert "user_id" not in created.json()

    detail = task_client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    completed = task_client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=headers,
        json={"notes": None, "is_completed": True},
    )
    deleted = task_client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
    missing = task_client.get(f"/api/v1/tasks/{task_id}", headers=headers)

    assert detail.status_code == 200
    assert completed.status_code == 200
    assert completed.json()["is_completed"] is True
    assert completed.json()["completed_at"].endswith("Z")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "task_not_found"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/tasks"),
        ("get", "/api/v1/tasks/missing"),
        ("patch", "/api/v1/tasks/missing"),
        ("delete", "/api/v1/tasks/missing"),
    ],
)
def test_all_task_operations_require_authentication(
    task_client: TestClient, method: str, path: str
) -> None:
    response = task_client.request(method, path, json={"title": "任务"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_foreign_and_missing_task_share_the_same_404(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    with task_client.app.state.database_session_factory() as session:
        other = User(username="other", password_hash="test-hash")
        session.add(other)
        session.flush()
        foreign = Task(user_id=other.id, title="其他用户任务")
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id

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
        {"title": "   "},
        {"title": "a" * 201},
        {"title": "ok", "notes": "a" * 4001},
        {"title": "ok", "due_at": "2026-07-30T18:00:00"},
        {"title": "ok", "completed_at": "2026-07-30T10:00:00Z"},
    ],
)
def test_create_validation_rejects_invalid_payloads_without_echo(
    task_client: TestClient, payload: dict[str, object]
) -> None:
    response = task_client.post(
        "/api/v1/tasks", headers=auth_headers(task_client), json=payload
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    sensitive_value = payload.get("notes")
    if isinstance(sensitive_value, str):
        assert sensitive_value not in response.text


def test_empty_patch_and_non_nullable_null_are_rejected(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)
    created = task_client.post(
        "/api/v1/tasks", headers=headers, json={"title": "task"}
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    for payload in ({}, {"title": None}, {"priority": None}, {"is_completed": None}):
        response = task_client.patch(
            f"/api/v1/tasks/{task_id}", headers=headers, json=payload
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_list_api_filters_and_continues_with_cursor(task_client: TestClient) -> None:
    headers = auth_headers(task_client)
    create_task_via_api(task_client, headers, "low", priority="low")
    high = create_task_via_api(task_client, headers, "high", priority="high")
    task_client.patch(
        f"/api/v1/tasks/{high['id']}",
        headers=headers,
        json={"is_completed": True},
    )

    first = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={"status": "all", "sort": "priority", "order": "desc", "limit": 1},
    )
    cursor = first.json()["next_cursor"]
    second = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={
            "status": "all",
            "sort": "priority",
            "order": "desc",
            "limit": 100,
            "cursor": cursor,
        },
    )
    active = task_client.get(
        "/api/v1/tasks", headers=headers, params={"status": "active"}
    )

    assert first.status_code == second.status_code == active.status_code == 200
    assert [item["title"] for item in first.json()["items"]] == ["high"]
    assert [item["title"] for item in second.json()["items"]] == ["low"]
    assert [item["title"] for item in active.json()["items"]] == ["low"]
    assert second.json()["next_cursor"] is None


def test_list_requires_authentication(task_client: TestClient) -> None:
    response = task_client.get("/api/v1/tasks")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    "params",
    [
        {"status": "unknown"},
        {"sort": "title"},
        {"order": "sideways"},
        {"limit": 0},
        {"limit": 101},
        {"unexpected": "value"},
    ],
)
def test_list_query_validation_is_stable_and_does_not_echo_input(
    task_client: TestClient, params: dict[str, object]
) -> None:
    headers = auth_headers(task_client)
    response = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params=params,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "unknown" not in response.text
    assert "sideways" not in response.text


def test_invalid_and_cross_query_cursor_use_stable_error(
    task_client: TestClient,
) -> None:
    headers = auth_headers(task_client)
    create_task_via_api(task_client, headers, "one")
    create_task_via_api(task_client, headers, "two")
    page = task_client.get(
        "/api/v1/tasks", headers=headers, params={"limit": 1}
    ).json()

    damaged = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={"cursor": "not-base64"},
    )
    crossed = task_client.get(
        "/api/v1/tasks",
        headers=headers,
        params={"status": "active", "cursor": page["next_cursor"]},
    )

    for response in (damaged, crossed):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cursor"
        assert response.json()["error"]["message"] == "分页游标无效"
        assert "not-base64" not in response.text


def test_openapi_contains_five_task_operations(task_client: TestClient) -> None:
    document = task_client.get("/openapi.json").json()
    paths = document["paths"]

    assert set(paths["/api/v1/tasks"]) == {"get", "post"}
    assert set(paths["/api/v1/tasks/{task_id}"]) == {"get", "patch", "delete"}
    for schema_name in ("TaskCreateRequest", "TaskUpdateRequest", "TaskResponse"):
        properties = document["components"]["schemas"][schema_name]["properties"]
        assert "user_id" not in properties
        assert "password_hash" not in properties
