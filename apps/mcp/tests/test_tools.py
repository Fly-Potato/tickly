"""Tickly MCP 只读工具的协议、转发与安全边界测试。"""

from datetime import UTC, datetime
import hashlib
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from app.config import Environment, Settings
from app.errors import McpToolError
from app.main import create_http_app, create_mcp_server
from app.schemas import (
    ParentOptionPagePayload,
    TaskDetailPayload,
    TaskListPayload,
    TopicListPayload,
)


TOKEN = "test-raw-token"
REQUEST_ID = "test-request-id"
SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807
READ_TOOL_NAMES = {
    "list_tasks",
    "get_task",
    "list_topics",
    "find_parent_tasks",
}


def task_payload(*, serial: int = 7, parent_id: str | None = None) -> dict[str, object]:
    """构造完整任务，确保结构化输出覆盖协议公开字段。"""
    timestamp = datetime(2026, 8, 19, 1, 2, 3, tzinfo=UTC).isoformat()
    return {
        "id": f"task-{serial}",
        "serial": serial,
        "title": f"任务 {serial}",
        "description": f"任务 {serial} 的描述",
        "priority": "medium",
        "topic": "工作",
        "status": "in_progress",
        "due_at": timestamp,
        "completed_at": None,
        "parent_id": parent_id,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


class FakeApiClient:
    """记录工具到上游 client 的参数，并返回严格响应模型。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error: McpToolError | None = None

    def _record(self, name: str, values: dict[str, object]) -> None:
        self.calls.append((name, values))
        if self.error is not None:
            raise self.error

    async def list_tasks(self, **values: object) -> TaskListPayload:
        self._record("list_tasks", values)
        root = task_payload(serial=7)
        child = task_payload(serial=8, parent_id="task-7")
        return TaskListPayload.model_validate(
            {
                "items": [
                    {
                        "task": root,
                        "children": [child],
                        "child_count": 1,
                        "completed_child_count": 0,
                        "context_only": False,
                    }
                ],
                "next_cursor": "next-page",
            }
        )

    async def get_task(self, **values: object) -> TaskDetailPayload:
        self._record("get_task", values)
        return TaskDetailPayload.model_validate(
            {
                **task_payload(serial=7),
                "children": [task_payload(serial=8, parent_id="task-7")],
            }
        )

    async def list_topics(self, **values: object) -> TopicListPayload:
        self._record("list_topics", values)
        return TopicListPayload(items=["工作", "个人"])

    async def find_parent_tasks(self, **values: object) -> ParentOptionPagePayload:
        self._record("find_parent_tasks", values)
        return ParentOptionPagePayload.model_validate(
            {
                "items": [
                    {
                        "id": "task-7",
                        "serial": 7,
                        "title": "任务 7",
                        "topic": "工作",
                        "status": "in_progress",
                    }
                ],
                "next_cursor": None,
            }
        )


def make_server(fake_api_client: FakeApiClient):
    settings = Settings(
        environment=Environment.TEST,
        token_sha256="a" * 64,
        _env_file=None,
    )
    return create_mcp_server(
        settings,
        api_client_override=fake_api_client,  # type: ignore[arg-type]
        security_context_provider=lambda context: (TOKEN, REQUEST_ID),
    )


@pytest.mark.asyncio
async def test_server_exposes_exact_read_tools_with_safe_annotations() -> None:
    async with Client(make_server(FakeApiClient())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == READ_TOOL_NAMES
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False


@pytest.mark.asyncio
async def test_read_tool_input_and_output_json_schemas_are_explicit() -> None:
    async with Client(make_server(FakeApiClient())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    list_schema = tools["list_tasks"].input_schema
    assert set(list_schema["properties"]) == {
        "status",
        "topic",
        "sort",
        "order",
        "cursor",
        "limit",
    }
    assert list_schema.get("required", []) == []
    assert list_schema["properties"]["status"]["default"] == "all"
    assert list_schema["properties"]["sort"]["default"] == "created_at"
    assert list_schema["properties"]["order"]["default"] == "desc"
    assert list_schema["properties"]["limit"] == {
        "default": 50,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }

    detail_schema = tools["get_task"].input_schema
    assert detail_schema["required"] == ["serial"]
    assert detail_schema["properties"]["serial"]["minimum"] == 1
    assert detail_schema["properties"]["serial"]["maximum"] == SQLITE_MAX_INTEGER

    assert tools["list_topics"].input_schema["properties"] == {}
    parent_schema = tools["find_parent_tasks"].input_schema
    assert set(parent_schema["properties"]) == {"query", "cursor", "limit"}
    assert parent_schema.get("required", []) == []
    assert parent_schema["properties"]["limit"]["default"] == 50

    expected_outputs = {
        "list_tasks": {"summary", "items", "next_cursor"},
        "get_task": {"summary", "task", "children"},
        "list_topics": {"summary", "items"},
        "find_parent_tasks": {"summary", "items", "next_cursor"},
    }
    for name, fields in expected_outputs.items():
        output_schema = tools[name].output_schema
        assert output_schema is not None
        assert set(output_schema["properties"]) == fields
        assert set(output_schema["required"]) == fields


@pytest.mark.asyncio
async def test_read_tools_forward_exact_arguments_token_and_request_id() -> None:
    fake = FakeApiClient()
    async with Client(make_server(fake)) as client:
        await client.call_tool(
            "list_tasks",
            {
                "status": "completed",
                "topic": "工作",
                "sort": "serial",
                "order": "asc",
                "cursor": "next-page",
                "limit": 25,
            },
        )
        await client.call_tool("get_task", {"serial": 7})
        await client.call_tool("list_topics", {})
        await client.call_tool(
            "find_parent_tasks",
            {"query": "#12 标题", "cursor": "parent-page", "limit": 10},
        )

    assert fake.calls == [
        (
            "list_tasks",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "status": "completed",
                "topic": "工作",
                "sort": "serial",
                "order": "asc",
                "cursor": "next-page",
                "limit": 25,
            },
        ),
        (
            "get_task",
            {"token": TOKEN, "request_id": REQUEST_ID, "serial": 7},
        ),
        ("list_topics", {"token": TOKEN, "request_id": REQUEST_ID}),
        (
            "find_parent_tasks",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "query": "#12 标题",
                "cursor": "parent-page",
                "limit": 10,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_read_tools_forward_stable_defaults() -> None:
    fake = FakeApiClient()
    async with Client(make_server(fake)) as client:
        await client.call_tool("list_tasks", {})
        await client.call_tool("find_parent_tasks", {})

    assert fake.calls == [
        (
            "list_tasks",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "status": "all",
                "topic": None,
                "sort": "created_at",
                "order": "desc",
                "cursor": None,
                "limit": 50,
            },
        ),
        (
            "find_parent_tasks",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "query": None,
                "cursor": None,
                "limit": 50,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_read_tools_return_machine_data_and_short_summaries() -> None:
    async with Client(make_server(FakeApiClient())) as client:
        listed = await client.call_tool("list_tasks", {})
        detail = await client.call_tool("get_task", {"serial": 7})
        topics = await client.call_tool("list_topics", {})
        parents = await client.call_tool("find_parent_tasks", {})

    assert listed.is_error is False
    assert listed.structured_content is not None
    assert listed.structured_content["summary"] == "已读取 1 个任务组"
    assert listed.structured_content["items"][0]["task"]["serial"] == 7
    assert listed.structured_content["next_cursor"] == "next-page"

    assert detail.structured_content is not None
    assert detail.structured_content["summary"] == "已读取任务 #7"
    assert detail.structured_content["task"]["serial"] == 7
    assert detail.structured_content["children"][0]["serial"] == 8

    assert topics.structured_content == {
        "summary": "已读取 2 个主题",
        "items": ["工作", "个人"],
    }
    assert parents.structured_content is not None
    assert parents.structured_content["summary"] == "已读取 1 个父任务候选"
    assert parents.structured_content["items"][0]["serial"] == 7


def test_request_security_context_reads_only_validated_headers() -> None:
    tools_module = import_module("app.tools")
    context = SimpleNamespace(
        headers={
            "authorization": f"Bearer {TOKEN}",
            "x-request-id": REQUEST_ID,
        }
    )

    assert tools_module.request_security_context(context) == (TOKEN, REQUEST_ID)


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {},
        {"authorization": "Basic secret"},
        {"authorization": "Bearer secret"},
        {
            "authorization": "Bearer secret",
            "x-request-id": "contains space",
        },
    ],
)
def test_request_security_context_fails_closed_without_echoing_headers(
    headers: dict[str, str] | None,
) -> None:
    tools_module = import_module("app.tools")

    with pytest.raises(McpToolError) as raised:
        tools_module.request_security_context(SimpleNamespace(headers=headers))

    assert raised.value.code == "authentication_required"
    assert raised.value.public_message == "需要 MCP 认证"
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_read_tool_propagates_stable_error_without_raw_token() -> None:
    fake = FakeApiClient()
    fake.error = McpToolError("upstream_unavailable", "Tickly API 暂时不可用")

    async with Client(make_server(fake)) as client:
        result = await client.call_tool("list_topics", {})

    assert result.is_error is True
    assert len(result.content) == 1
    error_text = result.content[0].text  # type: ignore[union-attr]
    assert "upstream_unavailable" in error_text
    assert "Tickly API 暂时不可用" in error_text
    assert TOKEN not in error_text


@pytest.mark.asyncio
async def test_streamable_http_forwards_configured_request_id_header() -> None:
    """真实 HTTP 协议调用必须沿用入口配置的 request ID header。"""
    fake = FakeApiClient()
    token_digest = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
    settings = Settings(
        environment=Environment.TEST,
        token_sha256=token_digest,
        request_id_header="X-Correlation-ID",
        allowed_hosts=["testserver"],
        allowed_origins=["https://codex.example"],
        api_base_url="http://api:8321",
        _env_file=None,
    )
    application = create_http_app(settings, api_client_override=fake)  # type: ignore[arg-type]
    protocol_app = application.app.app  # type: ignore[attr-defined]
    transport = httpx2.ASGITransport(app=application)

    # ASGITransport 不驱动 lifespan；显式进入真实 SDK Starlette 应用生命周期，
    # 让工具从与生产一致的 AppContext 取得覆盖注入的上游 client。
    async with protocol_app.router.lifespan_context(protocol_app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Correlation-ID": "correlation-request-1",
            },
        ) as http:
            client_transport = streamable_http_client(
                "http://testserver/mcp",
                http_client=http,
                terminate_on_close=False,
            )
            async with Client(client_transport) as client:
                result = await client.call_tool("list_topics", {})

    assert result.is_error is False
    assert result.structured_content == {
        "summary": "已读取 2 个主题",
        "items": ["工作", "个人"],
    }
    assert fake.calls == [
        (
            "list_topics",
            {"token": TOKEN, "request_id": "correlation-request-1"},
        )
    ]
