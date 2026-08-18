"""Tickly MCP 工具的协议、转发与安全边界测试。"""

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
    TaskPayload,
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
ALL_TOOL_NAMES = READ_TOOL_NAMES | {
    "create_task",
    "update_task",
    "set_task_status",
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

    async def create_task(self, **values: object) -> TaskPayload:
        self._record("create_task", values)
        return TaskPayload.model_validate(task_payload(serial=9))

    async def update_task(self, **values: object) -> TaskPayload:
        self._record("update_task", values)
        serial = values["serial"]
        assert isinstance(serial, int), "工具必须把合法 JSON integer 规范化为 int"
        return TaskPayload.model_validate(task_payload(serial=serial))


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

    assert READ_TOOL_NAMES <= set(tools)
    for name in READ_TOOL_NAMES:
        tool = tools[name]
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False


@pytest.mark.asyncio
async def test_server_exposes_exact_seven_tools_without_delete() -> None:
    """写工具集合固定为三个，删除能力不会通过命名或额外注册泄露。"""
    async with Client(make_server(FakeApiClient())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == ALL_TOOL_NAMES
    assert "delete_task" not in tools
    for name in ("create_task", "update_task", "set_task_status"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is False
        assert annotations.open_world_hint is False


@pytest.mark.asyncio
async def test_write_tool_input_and_output_schemas_are_restricted() -> None:
    """写工具只公开业务可写字段，状态变更必须走独立工具。"""
    async with Client(make_server(FakeApiClient())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    create_schema = tools["create_task"].input_schema
    assert create_schema["required"] == ["task"]
    create_input = create_schema["$defs"]["CreateTaskInput"]
    assert set(create_input["properties"]) == {
        "title",
        "description",
        "priority",
        "topic",
        "due_at",
        "parent_serial",
    }
    assert set(create_input["required"]) == {"title", "topic"}
    assert create_input["additionalProperties"] is False
    assert "status" not in create_input["properties"]
    assert "serial" not in create_input["properties"]

    update_schema = tools["update_task"].input_schema
    assert set(update_schema["required"]) == {"serial", "patch"}
    update_input = update_schema["$defs"]["UpdateTaskInput"]
    assert set(update_input["properties"]) == {
        "title",
        "description",
        "priority",
        "topic",
        "due_at",
        "parent_serial",
    }
    assert update_input.get("required", []) == []
    assert update_input["additionalProperties"] is False
    assert "status" not in update_input["properties"]

    status_schema = tools["set_task_status"].input_schema
    assert set(status_schema["properties"]) == {"serial", "status"}
    assert set(status_schema["required"]) == {"serial", "status"}
    assert set(status_schema["$defs"]["TaskStatus"]["enum"]) == {
        "new",
        "in_progress",
        "completed",
    }

    for name in ("create_task", "update_task", "set_task_status"):
        output_schema = tools[name].output_schema
        assert output_schema is not None
        assert set(output_schema["properties"]) == {"summary", "task"}
        assert set(output_schema["required"]) == {"summary", "task"}


@pytest.mark.asyncio
async def test_write_tools_forward_exact_payloads_and_return_structured_results() -> None:
    """创建、普通 patch 与状态 patch 必须逐字段转发认证上下文和机器结果。"""
    fake = FakeApiClient()
    async with Client(make_server(fake)) as client:
        created = await client.call_tool(
            "create_task",
            {
                "task": {
                    "title": "发布版本",
                    "description": "完成发布检查",
                    "priority": "high",
                    "topic": "工作",
                    "due_at": "2026-08-20T10:30:00+08:00",
                    "parent_serial": 7.0,
                }
            },
        )
        updated = await client.call_tool(
            "update_task",
            {
                "serial": 9.0,
                "patch": {"title": "已复核发布", "priority": "medium"},
            },
        )
        status_changed = await client.call_tool(
            "set_task_status",
            {"serial": 9.0, "status": "completed"},
        )

    assert fake.calls == [
        (
            "create_task",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "payload": {
                    "title": "发布版本",
                    "description": "完成发布检查",
                    "priority": "high",
                    "topic": "工作",
                    "due_at": "2026-08-20T10:30:00+08:00",
                    "parent_serial": 7,
                },
            },
        ),
        (
            "update_task",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "serial": 9,
                "patch": {"title": "已复核发布", "priority": "medium"},
            },
        ),
        (
            "update_task",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "serial": 9,
                "patch": {"status": "completed"},
            },
        ),
    ]
    assert created.is_error is False
    assert created.structured_content is not None
    assert created.structured_content["summary"] == "已创建任务 #9"
    assert created.structured_content["task"]["serial"] == 9
    assert updated.structured_content is not None
    assert updated.structured_content["summary"] == "已更新任务 #9"
    assert status_changed.structured_content is not None
    assert status_changed.structured_content["summary"] == "已更新任务 #9 的状态"


@pytest.mark.asyncio
async def test_create_omits_defaults_and_update_preserves_explicit_nulls() -> None:
    """省略字段不进入上游 JSON，而三个 nullable patch 的 null 必须原样保留。"""
    fake = FakeApiClient()
    async with Client(make_server(fake)) as client:
        await client.call_tool(
            "create_task",
            {"task": {"title": "最小创建", "topic": "个人"}},
        )
        await client.call_tool(
            "update_task",
            {
                "serial": 9,
                "patch": {
                    "priority": None,
                    "due_at": None,
                    "parent_serial": None,
                },
            },
        )

    assert fake.calls == [
        (
            "create_task",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "payload": {"title": "最小创建", "topic": "个人"},
            },
        ),
        (
            "update_task",
            {
                "token": TOKEN,
                "request_id": REQUEST_ID,
                "serial": 9,
                "patch": {
                    "priority": None,
                    "due_at": None,
                    "parent_serial": None,
                },
            },
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "create_task",
            {"task": {"title": "非法状态", "topic": "工作", "status": "new"}},
        ),
        (
            "create_task",
            {"task": {"title": "服务端字段", "topic": "工作", "serial": 99}},
        ),
        (
            "update_task",
            {"serial": 9, "patch": {"status": "completed"}},
        ),
        (
            "update_task",
            {"serial": 9, "patch": {}},
        ),
        (
            "update_task",
            {"serial": 9, "patch": {"title": None}},
        ),
        (
            "update_task",
            {"serial": 9, "patch": {"description": None}},
        ),
        (
            "update_task",
            {"serial": 9, "patch": {"topic": None}},
        ),
        (
            "set_task_status",
            {"serial": 9, "status": "archived"},
        ),
        (
            "create_task",
            {
                "task": {
                    "title": "无时区截止时间",
                    "topic": "工作",
                    "due_at": "2026-08-20T10:30:00",
                }
            },
        ),
        (
            "update_task",
            {
                "serial": 9,
                "patch": {"due_at": "2026-08-20T10:30:00"},
            },
        ),
    ],
    ids=[
        "create-status",
        "create-server-field",
        "update-status",
        "update-empty",
        "update-null-title",
        "update-null-description",
        "update-null-topic",
        "invalid-status",
        "create-naive-datetime",
        "update-naive-datetime",
    ],
)
async def test_write_tools_reject_forbidden_fields_and_invalid_values(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """非法写入在调用上游前失败，不能依赖 API 再兜底拒绝。"""
    fake = FakeApiClient()

    async with Client(make_server(fake)) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is True
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "create_task",
            {"task": {"title": "字符串父级", "topic": "工作", "parent_serial": "7"}},
        ),
        (
            "create_task",
            {"task": {"title": "布尔父级", "topic": "工作", "parent_serial": True}},
        ),
        (
            "update_task",
            {"serial": 9, "patch": {"parent_serial": 7.1}},
        ),
        (
            "set_task_status",
            {"serial": "9", "status": "new"},
        ),
    ],
    ids=["parent-string", "parent-bool", "parent-fraction", "serial-string"],
)
async def test_write_integer_inputs_reject_non_json_integer_semantics(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """流水号沿用只读工具的 JSON integer 规范，不接受字符串、布尔或小数。"""
    fake = FakeApiClient()

    async with Client(make_server(fake)) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is True
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["create_task", "update_task", "set_task_status"])
async def test_write_tools_propagate_stable_errors_without_raw_token(tool_name: str) -> None:
    """三个写入口都只返回稳定错误，不得在协议错误内容中回显明文 Token。"""
    fake = FakeApiClient()
    fake.error = McpToolError("upstream_unavailable", "Tickly API 暂时不可用")
    arguments = {
        "create_task": {"task": {"title": "创建", "topic": "工作"}},
        "update_task": {"serial": 9, "patch": {"title": "更新"}},
        "set_task_status": {"serial": 9, "status": "new"},
    }[tool_name]

    async with Client(make_server(fake)) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is True
    assert len(result.content) == 1
    error_text = result.content[0].text  # type: ignore[union-attr]
    assert "upstream_unavailable" in error_text
    assert "Tickly API 暂时不可用" in error_text
    assert TOKEN not in error_text


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
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_task", {"serial": "7"}),
        ("get_task", {"serial": True}),
        ("get_task", {"serial": 7.1}),
        ("get_task", {"serial": float("inf")}),
        ("get_task", {"serial": float("nan")}),
        ("list_tasks", {"limit": "25"}),
        ("list_tasks", {"limit": True}),
        ("list_tasks", {"limit": 25.1}),
        ("list_tasks", {"limit": float("inf")}),
        ("list_tasks", {"limit": float("nan")}),
    ],
    ids=[
        "serial-string",
        "serial-bool",
        "serial-non-integral-float",
        "serial-infinite-float",
        "serial-nan",
        "limit-string",
        "limit-bool",
        "limit-non-integral-float",
        "limit-infinite-float",
        "limit-nan",
    ],
)
async def test_integer_inputs_reject_coercion_before_calling_upstream(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """流水号和分页上限只接受 JSON integer，不把其他类型强制转换。"""
    fake = FakeApiClient()

    async with Client(make_server(fake)) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is True
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_call"),
    [
        (
            "get_task",
            {"serial": 7.0},
            (
                "get_task",
                {"token": TOKEN, "request_id": REQUEST_ID, "serial": 7},
            ),
        ),
        (
            "list_tasks",
            {"limit": 25.0},
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
                    "limit": 25,
                },
            ),
        ),
    ],
    ids=["serial-integral-float", "limit-integral-float"],
)
async def test_integral_float_inputs_are_normalized_to_integer(
    tool_name: str,
    arguments: dict[str, object],
    expected_call: tuple[str, dict[str, object]],
) -> None:
    """JSON Schema integer 包含整值浮点，转发前统一规范化为 Python int。"""
    fake = FakeApiClient()

    async with Client(make_server(fake)) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is False
    assert fake.calls == [expected_call]
    forwarded_value = fake.calls[0][1][next(iter(arguments))]
    assert type(forwarded_value) is int


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
