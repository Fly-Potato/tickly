import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
import logging
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings, create_session_factory
from app.main import create_app
from app.models import Task, User
from app.services.accounts import create_account
from app.services.mcp_tokens import create_mcp_token, revoke_mcp_token


PASSWORD = "correct horse battery staple"


@pytest.fixture
def mcp_client(tmp_path: Path) -> Iterator[TestClient]:
    """创建由数据库 Token 认证的真实 HTTP 测试应用。"""

    database_url = f"sqlite:///{tmp_path / 'mcp-tasks-api.db'}"
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
        user = create_account(session, "potato", PASSWORD)
        issued = create_mcp_token(session, user.id, "测试 MCP", None)
    app = create_app(settings, database_engine=engine)
    app.state.test_database_url = database_url
    app.state.test_mcp_raw_token = issued.raw_token
    app.state.test_mcp_token_id = issued.record.id
    app.state.test_mcp_user_id = user.id
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    engine.dispose()


@pytest.fixture
def mcp_headers(mcp_client: TestClient) -> dict[str, str]:
    return bearer(mcp_client.app.state.test_mcp_raw_token)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


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
    assert response.json()["error"]["message"] == "需要 MCP 认证"
    assert response.headers["www-authenticate"] == "Bearer"


def test_mcp_token_cannot_access_public_task_api(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    response = mcp_client.get("/api/v1/tasks", headers=mcp_headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_cli_created_users_with_same_serial_are_isolated_over_http(
    mcp_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 CLI 账号、Web 签发和内部资源请求共同证明账号级隔离。"""

    monkeypatch.setenv(
        "TICKLY_DATABASE_URL", mcp_client.app.state.test_database_url
    )
    monkeypatch.setenv("TICKLY_ENVIRONMENT", "test")
    answers = iter([PASSWORD, PASSWORD, PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda _: next(answers))
    assert cli_main(["user", "create", "--username", "cli-first"]) == 0
    assert cli_main(["user", "create", "--username", "cli-second"]) == 0

    first_access = login(mcp_client, "cli-first")
    second_access = login(mcp_client, "cli-second")
    first_token_response = mcp_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(first_access),
        json={"name": "第一账号 MCP", "expires_in_days": 365},
    )
    second_token_response = mcp_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(second_access),
        json={"name": "第二账号 MCP", "expires_in_days": None},
    )
    assert first_token_response.status_code == second_token_response.status_code == 201
    first_mcp = first_token_response.json()["token"]
    second_mcp = second_token_response.json()["token"]

    first_task = mcp_client.post(
        "/api/v1/tasks",
        headers=bearer(first_access),
        json={"title": "第一账号同号任务", "topic": "隔离"},
    )
    second_task = mcp_client.post(
        "/api/v1/tasks",
        headers=bearer(second_access),
        json={"title": "第二账号同号任务", "topic": "隔离"},
    )
    assert first_task.status_code == second_task.status_code == 201
    assert first_task.json()["serial"] == second_task.json()["serial"] == 1

    first_detail = mcp_client.get(
        "/internal/mcp/v1/tasks/1", headers=bearer(first_mcp)
    )
    second_detail = mcp_client.get(
        "/internal/mcp/v1/tasks/1", headers=bearer(second_mcp)
    )
    web_blocked = mcp_client.get(
        "/internal/mcp/v1/tasks/1", headers=bearer(first_access)
    )
    mcp_blocked = mcp_client.get(
        "/api/v1/tasks", headers=bearer(first_mcp)
    )

    assert first_detail.status_code == second_detail.status_code == 200
    assert first_detail.json()["title"] == "第一账号同号任务"
    assert second_detail.json()["title"] == "第二账号同号任务"
    assert "第二账号同号任务" not in first_detail.text
    assert "第一账号同号任务" not in second_detail.text
    assert web_blocked.status_code == mcp_blocked.status_code == 401
    assert web_blocked.json()["error"]["code"] == "authentication_required"
    assert mcp_blocked.json()["error"]["code"] == "authentication_required"


def test_task_route_revalidates_after_transport_verification(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    """transport 验证后撤销 Token，资源请求必须重新认证而不能复用结果。"""

    add_task(mcp_client, serial=1, title="撤销竞态任务")
    verified = mcp_client.post(
        "/internal/mcp/v1/auth/verify",
        headers=mcp_headers,
    )
    assert verified.status_code == 204
    assert verified.content == b""

    with mcp_client.app.state.database_session_factory() as session:
        revoke_mcp_token(
            session,
            mcp_client.app.state.test_mcp_user_id,
            mcp_client.app.state.test_mcp_token_id,
        )

    blocked = mcp_client.get(
        "/internal/mcp/v1/tasks/1",
        headers=mcp_headers,
    )

    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"
    assert blocked.headers["www-authenticate"] == "Bearer"


def test_mcp_verify_preserves_protocol_id_but_logs_server_id(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MCP→API 验证链路透传协议 ID，但访问日志不得写入该可控值。"""
    protocol_request_id = mcp_client.app.state.test_mcp_raw_token
    caplog.clear()
    root_logger = logging.getLogger()
    access_logger = logging.getLogger("tickly.access")
    access_was_disabled = access_logger.disabled
    access_logger.disabled = False
    root_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="tickly.access"):
            response = mcp_client.post(
                "/internal/mcp/v1/auth/verify",
                headers={**mcp_headers, "X-Request-ID": protocol_request_id},
            )
    finally:
        root_logger.removeHandler(caplog.handler)
        access_logger.disabled = access_was_disabled

    records = [
        record
        for record in caplog.records
        if record.name == "tickly.access"
        and record.getMessage() == "request.completed"
    ]
    assert response.status_code == 204
    assert response.headers["X-Request-ID"] == protocol_request_id
    assert len(records) == 1
    assert records[0].request_id != protocol_request_id
    UUID(records[0].request_id)
    assert protocol_request_id not in caplog.text


def test_failed_mcp_authentication_keeps_request_id_and_hides_credentials(
    mcp_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """认证失败响应和安全日志都不得包含 PAT、摘要或 Web JWT。"""

    caplog.set_level(logging.INFO)
    with mcp_client.app.state.database_session_factory() as session:
        issued = create_mcp_token(
            session,
            mcp_client.app.state.test_mcp_user_id,
            "日志脱敏",
            None,
        )
        raw_pat = issued.raw_token
        digest = issued.record.token_hash
        revoke_mcp_token(
            session,
            mcp_client.app.state.test_mcp_user_id,
            issued.record.id,
        )
    web_jwt = login(mcp_client, "potato")

    pat_response = mcp_client.get(
        "/internal/mcp/v1/tasks/1",
        headers={**bearer(raw_pat), "X-Request-ID": "mcp-pat-denied"},
    )
    jwt_response = mcp_client.get(
        "/internal/mcp/v1/tasks/1",
        headers={**bearer(web_jwt), "X-Request-ID": "mcp-jwt-denied"},
    )

    assert pat_response.status_code == jwt_response.status_code == 401
    assert pat_response.json()["error"]["request_id"] == "mcp-pat-denied"
    assert jwt_response.json()["error"]["request_id"] == "mcp-jwt-denied"
    rendered = pat_response.text + jwt_response.text + caplog.text
    for hidden in (raw_pat, digest, web_jwt):
        assert hidden not in rendered


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


def test_internal_topics_and_parent_options_keep_owned_route_semantics(
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
        other = create_account(session, "other", PASSWORD)
        other_issued = create_mcp_token(session, other.id, "其他账号 MCP", None)
        other_id = other.id
    add_task(
        mcp_client,
        serial=1,
        title="其他账号同号任务",
        user_id=other_id,
    )

    owned_response = mcp_client.get(
        "/internal/mcp/v1/tasks/1",
        headers=mcp_headers,
    )
    other_response = mcp_client.get(
        "/internal/mcp/v1/tasks/1",
        headers=bearer(other_issued.raw_token),
    )
    missing_response = mcp_client.get(
        "/internal/mcp/v1/tasks/999",
        headers=mcp_headers,
    )

    assert owned_response.status_code == other_response.status_code == 200
    assert owned_response.json()["title"] == "账号内任务"
    assert other_response.json()["title"] == "其他账号同号任务"
    assert "其他账号同号任务" not in owned_response.text
    assert "账号内任务" not in other_response.text
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


def test_settings_routes_are_public_but_internal_mcp_routes_stay_hidden(
    mcp_client: TestClient,
) -> None:
    paths = mcp_client.get("/openapi.json").json()["paths"]

    assert "/api/v1/account/password" in paths
    assert "/api/v1/mcp-tokens" in paths
    assert "/internal/mcp/v1/auth/verify" not in paths
    assert "/internal/mcp/v1/tasks/{serial}" not in paths


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
        other = create_account(session, "other-parent", PASSWORD)
        other_id = other.id
    add_task(
        mcp_client,
        serial=1,
        title="其他账号父任务",
        user_id=other_id,
    )

    response = mcp_client.post(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        json={"title": "越权子任务", "topic": "工作", "parent_serial": 1},
    )

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


def test_internal_cancelled_parent_cascades_pending_children_and_reports_counts(
    mcp_client: TestClient,
    mcp_headers: dict[str, str],
) -> None:
    """内部 Bearer 契约按流水号废弃父任务，并保留已完成子任务。"""

    parent = add_task(mcp_client, serial=1, title="父任务")
    new_child = add_task(
        mcp_client,
        serial=2,
        title="未开始子任务",
        parent_id=parent.id,
    )
    progressing_child = add_task(
        mcp_client,
        serial=3,
        title="进行中子任务",
        status="in_progress",
        parent_id=parent.id,
    )
    completed_child = add_task(
        mcp_client,
        serial=4,
        title="已完成子任务",
        parent_id=parent.id,
    )
    completed = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{completed_child.serial}",
        headers=mcp_headers,
        json={"status": "completed"},
    ).json()

    cancelled = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{parent.serial}",
        headers=mcp_headers,
        json={"status": "cancelled"},
    )
    repeated = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{parent.serial}",
        headers=mcp_headers,
        json={"status": "cancelled"},
    )
    detail = mcp_client.get(
        f"/internal/mcp/v1/tasks/{parent.serial}", headers=mcp_headers
    )
    filtered = mcp_client.get(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        params={"status": "cancelled", "sort": "serial", "order": "asc"},
    )

    assert cancelled.status_code == repeated.status_code == 200
    assert cancelled.json()["status"] == repeated.json()["status"] == "cancelled"
    assert cancelled.json()["completed_at"] is None
    children_by_serial = {
        child["serial"]: child for child in detail.json()["children"]
    }
    assert children_by_serial[new_child.serial]["status"] == "cancelled"
    assert children_by_serial[progressing_child.serial]["status"] == "cancelled"
    assert children_by_serial[completed_child.serial]["status"] == "completed"
    assert children_by_serial[completed_child.serial]["completed_at"] == completed[
        "completed_at"
    ]
    assert children_by_serial[completed_child.serial]["updated_at"] == completed[
        "updated_at"
    ]
    assert filtered.status_code == 200
    group = filtered.json()["items"][0]
    assert group["task"]["serial"] == parent.serial
    assert group["child_count"] == 3
    assert group["completed_child_count"] == 1
    assert group["resolved_child_count"] == 3

    restored = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{parent.serial}",
        headers=mcp_headers,
        json={"status": "new"},
    )
    restored_detail = mcp_client.get(
        f"/internal/mcp/v1/tasks/{parent.serial}", headers=mcp_headers
    )

    assert restored.status_code == 200
    assert restored.json()["status"] == "new"
    assert {
        child["serial"]: child["status"]
        for child in restored_detail.json()["children"]
    } == {
        new_child.serial: "cancelled",
        progressing_child.serial: "cancelled",
        completed_child.serial: "completed",
    }


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
