"""MCP 到 Tickly API 单一 HTTP 边界的契约测试。"""

from collections.abc import AsyncIterator, Awaitable, Callable
import asyncio
from datetime import UTC, datetime
import json

import httpx
import pytest

from app.api_client import TicklyApiClient
from app.errors import McpToolError


TOKEN = "raw-token"
REQUEST_ID = "request-1"
BASE_URL = "http://api:8321"


class TrackingByteStream(httpx.AsyncByteStream):
    """记录响应分块消费和关闭状态，验证超限后不会继续读取。"""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        read_error: httpx.RequestError | None = None,
    ) -> None:
        self.chunks = chunks
        self.read_error = read_error
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk
        if self.read_error is not None:
            raise self.read_error

    async def aclose(self) -> None:
        self.closed = True


def task_payload(*, serial: int = 7) -> dict[str, object]:
    """生成完整任务响应，确保 client 校验真实内部 API 契约。"""
    timestamp = datetime(2026, 8, 19, 1, 2, 3, tzinfo=UTC).isoformat()
    return {
        "id": f"task-{serial}",
        "serial": serial,
        "title": f"任务 {serial}",
        "description": "任务描述",
        "priority": "medium",
        "topic": "工作",
        "status": "in_progress",
        "due_at": timestamp,
        "completed_at": None,
        "parent_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


async def call_with_transport(
    handler: httpx.AsyncBaseTransport | Callable[[httpx.Request], httpx.Response],
    operation: Callable[[TicklyApiClient], Awaitable[object]],
    *,
    max_response_bytes: int = 1_048_576,
    request_id_header: str = "X-Request-ID",
) -> object:
    """用真实 HTTPX 请求构造和解码路径驱动 client。"""
    transport = (
        handler
        if isinstance(handler, httpx.AsyncBaseTransport)
        else httpx.MockTransport(handler)
    )
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        client = TicklyApiClient(
            http,
            max_response_bytes=max_response_bytes,
            request_id_header=request_id_header,
        )
        return await operation(client)


@pytest.mark.asyncio
async def test_verify_token_requires_exact_empty_204_and_forwards_security_context() -> None:
    """认证验证必须逐请求透传原 PAT 与请求 ID，且只接受空 204。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    await call_with_transport(
        handler,
        lambda client: client.verify_token(token=TOKEN, request_id=REQUEST_ID),
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/internal/mcp/v1/auth/verify"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Request-ID"] == REQUEST_ID
    assert request.content == b""


@pytest.mark.asyncio
async def test_verify_token_uses_configured_request_id_header() -> None:
    """自定义关联头必须同时用于 transport 验证到 API 的请求。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    await call_with_transport(
        handler,
        lambda client: client.verify_token(token=TOKEN, request_id=REQUEST_ID),
        request_id_header="X-Correlation-ID",
    )

    assert requests[0].headers["X-Correlation-ID"] == REQUEST_ID
    assert "X-Request-ID" not in requests[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            httpx.Response(
                401,
                json={"error": {"code": "authentication_required"}},
            ),
            "authentication_required",
        ),
        (
            httpx.Response(503, json={"error": {"code": "database_busy"}}),
            "upstream_unavailable",
        ),
        (httpx.Response(200, json={}), "upstream_contract_error"),
        (httpx.Response(204, content=b"unexpected"), "upstream_contract_error"),
        (
            httpx.Response(401, json={"error": {"code": "unknown"}}),
            "upstream_contract_error",
        ),
        (
            httpx.Response(401, json={"error": {"code": "task_not_found"}}),
            "upstream_contract_error",
        ),
    ],
    ids=[
        "invalid-token",
        "upstream-503",
        "status-200",
        "204-with-body",
        "unknown-401",
        "non-auth-401",
    ],
)
async def test_verify_token_maps_failures_without_echoing_token(
    response: httpx.Response,
    expected_code: str,
) -> None:
    """认证端点异常只能投影为固定错误，不能携带 PAT 或上游正文。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.verify_token(token=TOKEN, request_id=REQUEST_ID),
        )

    assert calls == 1
    assert raised.value.code == expected_code
    assert TOKEN not in str(raised.value)
    assert "database_busy" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_verify_token_maps_network_and_oversize_without_retry() -> None:
    """认证网络失败与超限响应都只请求一次并执行脱敏映射。"""
    network_calls = 0

    def network_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        raise httpx.ConnectError(f"内部地址 {TOKEN}", request=request)

    with pytest.raises(McpToolError) as network_error:
        await call_with_transport(
            network_handler,
            lambda client: client.verify_token(token=TOKEN, request_id=REQUEST_ID),
        )

    oversize_calls = 0

    def oversize_handler(request: httpx.Request) -> httpx.Response:
        nonlocal oversize_calls
        oversize_calls += 1
        return httpx.Response(204, content=b"too-large")

    with pytest.raises(McpToolError) as oversize_error:
        await call_with_transport(
            oversize_handler,
            lambda client: client.verify_token(token=TOKEN, request_id=REQUEST_ID),
            max_response_bytes=4,
        )

    assert network_calls == oversize_calls == 1
    assert network_error.value.code == "upstream_unavailable"
    assert oversize_error.value.code == "upstream_contract_error"
    assert TOKEN not in str(network_error.value)
    assert network_error.value.__cause__ is None
    assert network_error.value.__context__ is None


@pytest.mark.asyncio
async def test_concurrent_verifications_keep_user_tokens_separate() -> None:
    """共享 client 的并发验证不得把不同用户的安全上下文写入实例状态。"""
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        requests.append(
            (request.headers["Authorization"], request.headers["X-Request-ID"])
        )
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        client = TicklyApiClient(
            http,
            max_response_bytes=1_048_576,
            request_id_header="X-Request-ID",
        )
        await asyncio.gather(
            client.verify_token(
                token="tickly_mcp_first.secret",
                request_id="req-first",
            ),
            client.verify_token(
                token="tickly_mcp_second.secret",
                request_id="req-second",
            ),
        )

    assert set(requests) == {
        ("Bearer tickly_mcp_first.secret", "req-first"),
        ("Bearer tickly_mcp_second.secret", "req-second"),
    }


@pytest.mark.asyncio
async def test_list_tasks_forwards_query_and_security_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    result = await call_with_transport(
        handler,
        lambda client: client.list_tasks(
            token=TOKEN,
            request_id=REQUEST_ID,
            status="all",
            topic=None,
            sort="created_at",
            order="desc",
            cursor=None,
            limit=50,
        ),
    )

    assert result.next_cursor is None
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/internal/mcp/v1/tasks"
    assert dict(request.url.params) == {
        "status": "all",
        "sort": "created_at",
        "order": "desc",
        "limit": "50",
    }
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Request-ID"] == REQUEST_ID


@pytest.mark.asyncio
async def test_list_tasks_validates_resolved_child_count_contract() -> None:
    """任务组必须接收服务端提供的已处理子任务计数。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "task": task_payload(),
                        "children": [],
                        "child_count": 0,
                        "completed_child_count": 0,
                        "resolved_child_count": 0,
                        "context_only": False,
                    }
                ],
                "next_cursor": None,
            },
        )

    result = await call_with_transport(
        handler,
        lambda client: client.list_tasks(
            token=TOKEN,
            request_id=REQUEST_ID,
            status="all",
            topic=None,
            sort="created_at",
            order="desc",
            cursor=None,
            limit=50,
        ),
    )

    assert result.items[0].resolved_child_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "method", "path", "query", "body"),
    [
        (
            lambda client: client.get_task(
                token=TOKEN, request_id=REQUEST_ID, serial=7
            ),
            "GET",
            "/internal/mcp/v1/tasks/7",
            {},
            None,
        ),
        (
            lambda client: client.list_topics(token=TOKEN, request_id=REQUEST_ID),
            "GET",
            "/internal/mcp/v1/tasks/topics",
            {},
            None,
        ),
        (
            lambda client: client.find_parent_tasks(
                token=TOKEN,
                request_id=REQUEST_ID,
                query="#12 标题",
                cursor="next-page",
                limit=25,
            ),
            "GET",
            "/internal/mcp/v1/tasks/parent-options",
            {"query": "#12 标题", "cursor": "next-page", "limit": "25"},
            None,
        ),
        (
            lambda client: client.create_task(
                token=TOKEN,
                request_id=REQUEST_ID,
                payload={"title": "新任务", "topic": "工作", "parent_serial": 2},
            ),
            "POST",
            "/internal/mcp/v1/tasks",
            {},
            {"title": "新任务", "topic": "工作", "parent_serial": 2},
        ),
        (
            lambda client: client.update_task(
                token=TOKEN,
                request_id=REQUEST_ID,
                serial=7,
                patch={"priority": None, "due_at": None},
            ),
            "PATCH",
            "/internal/mcp/v1/tasks/7",
            {},
            {"priority": None, "due_at": None},
        ),
        (
            lambda client: client.update_task(
                token=TOKEN,
                request_id=REQUEST_ID,
                serial=7,
                patch={"status": "cancelled"},
            ),
            "PATCH",
            "/internal/mcp/v1/tasks/7",
            {},
            {"status": "cancelled"},
        ),
    ],
    ids=["detail", "topics", "parents", "create", "update", "cancelled-status"],
)
async def test_api_operations_use_exact_http_contract(
    operation: Callable[[TicklyApiClient], Awaitable[object]],
    method: str,
    path: str,
    query: dict[str, str],
    body: dict[str, object] | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if path.endswith("/topics"):
            return httpx.Response(200, json={"items": ["工作"]})
        if path.endswith("/parent-options"):
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if method == "GET":
            return httpx.Response(200, json={**task_payload(), "children": []})
        return httpx.Response(200, json=task_payload())

    await call_with_transport(handler, operation)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == method
    assert request.url.path == path
    assert dict(request.url.params) == query
    if body is not None:
        assert json.loads(request.content) == body
    else:
        assert not request.content
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Request-ID"] == REQUEST_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected_code", "expected_message"),
    [
        (401, {"error": {"code": "authentication_required"}}, "authentication_required", "需要 MCP 认证"),
        (404, {"error": {"code": "task_not_found"}}, "task_not_found", "任务不存在"),
        (422, {"error": {"code": "validation_error"}}, "validation_error", "请求参数无效"),
        (422, {"error": {"code": "invalid_cursor"}}, "invalid_cursor", "分页游标无效"),
        (422, {"error": {"code": "invalid_task_relationship"}}, "invalid_task_relationship", "父待办关系无效"),
        (503, {"error": {"code": "mcp_account_unavailable"}}, "upstream_unavailable", "Tickly API 暂时不可用"),
        (503, {"error": {"code": "unexpected"}}, "upstream_unavailable", "Tickly API 暂时不可用"),
        (418, {"error": {"code": "unexpected"}}, "upstream_contract_error", "Tickly API 返回了无效响应"),
    ],
)
async def test_api_errors_map_to_stable_public_errors_without_retry(
    status_code: int,
    body: dict[str, object],
    expected_code: str,
    expected_message: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json=body)

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.create_task(
                token=TOKEN,
                request_id=REQUEST_ID,
                payload={"title": "不会泄漏的正文", "topic": "秘密主题"},
            ),
        )

    assert calls == 1
    assert raised.value.code == expected_code
    assert raised.value.public_message == expected_message
    error_text = str(raised.value)
    assert TOKEN not in error_text
    assert "不会泄漏的正文" not in error_text
    assert BASE_URL not in error_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "known_code",
    [
        "authentication_required",
        "task_not_found",
        "invalid_cursor",
        "invalid_task_relationship",
        "validation_error",
    ],
)
async def test_server_errors_cannot_impersonate_known_business_errors(
    known_code: str,
) -> None:
    """任何 5xx 都必须先映射不可用，不能借正文伪装成可处理的 4xx。"""
    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            lambda request: httpx.Response(
                500,
                json={"error": {"code": known_code}},
            ),
            lambda client: client.list_topics(
                token=TOKEN,
                request_id=REQUEST_ID,
            ),
        )

    assert raised.value.code == "upstream_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_error", "expected_code"),
    [
        (httpx.ReadTimeout("包含内部细节和 raw-token"), "upstream_unavailable"),
        (httpx.ConnectError("http://api:8321 raw-token"), "upstream_unavailable"),
    ],
    ids=["timeout", "connection"],
)
async def test_transport_errors_are_safe_and_never_retried(
    transport_error: httpx.RequestError,
    expected_code: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        transport_error.request = request
        raise transport_error

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.update_task(
                token=TOKEN,
                request_id=REQUEST_ID,
                serial=7,
                patch={"title": "不能重试的写操作"},
            ),
        )

    assert calls == 1
    assert raised.value.code == expected_code
    assert raised.value.public_message == "Tickly API 暂时不可用"
    assert TOKEN not in str(raised.value)
    assert BASE_URL not in str(raised.value)
    # 底层 HTTPX 异常持有含 Authorization 的 request，不得挂到公开异常链上。
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "max_response_bytes"),
    [
        (httpx.Response(200, content=b"not-json"), 1_048_576),
        (httpx.Response(200, json=["not", "an", "object"]), 1_048_576),
        (httpx.Response(200, json={"items": [], "next_cursor": None}), 8),
    ],
    ids=["non-json", "non-object", "too-large"],
)
async def test_invalid_response_envelope_maps_to_contract_error(
    response: httpx.Response,
    max_response_bytes: int,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.list_tasks(
                token=TOKEN,
                request_id=REQUEST_ID,
                status="all",
                topic=None,
                sort="created_at",
                order="desc",
                cursor=None,
                limit=50,
            ),
            max_response_bytes=max_response_bytes,
        )

    assert calls == 1
    assert raised.value.code == "upstream_contract_error"
    assert raised.value.public_message == "Tickly API 返回了无效响应"


@pytest.mark.asyncio
async def test_response_limit_stops_stream_immediately_and_closes_it() -> None:
    stream = TrackingByteStream([b"12345", b"6", b"must-not-be-consumed"])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=stream)

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.list_topics(token=TOKEN, request_id=REQUEST_ID),
            max_response_bytes=5,
        )

    assert calls == 1
    assert stream.consumed == 2
    assert stream.closed is True
    assert raised.value.code == "upstream_contract_error"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "read_error",
    [
        httpx.ReadTimeout("raw-token 响应读取超时"),
        httpx.ReadError("http://api:8321 响应读取失败"),
    ],
    ids=["timeout", "request-error"],
)
async def test_stream_read_errors_are_safe_and_never_retried(
    read_error: httpx.RequestError,
) -> None:
    stream = TrackingByteStream([b'{"items":'], read_error=read_error)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=stream)

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.list_topics(token=TOKEN, request_id=REQUEST_ID),
        )

    assert calls == 1
    assert stream.consumed == 1
    assert stream.closed is True
    assert raised.value.code == "upstream_unavailable"
    assert TOKEN not in str(raised.value)
    assert BASE_URL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        lambda client: client.list_tasks(
            token=TOKEN,
            request_id=REQUEST_ID,
            status="all",
            topic=None,
            sort="created_at",
            order="desc",
            cursor=None,
            limit=50,
        ),
        lambda client: client.get_task(token=TOKEN, request_id=REQUEST_ID, serial=7),
        lambda client: client.list_topics(token=TOKEN, request_id=REQUEST_ID),
        lambda client: client.find_parent_tasks(
            token=TOKEN, request_id=REQUEST_ID, query=None, cursor=None, limit=50
        ),
        lambda client: client.create_task(
            token=TOKEN,
            request_id=REQUEST_ID,
            payload={"title": "任务", "topic": "工作"},
        ),
        lambda client: client.update_task(
            token=TOKEN,
            request_id=REQUEST_ID,
            serial=7,
            patch={"title": "任务"},
        ),
    ],
    ids=["list", "detail", "topics", "parents", "create", "update"],
)
async def test_each_typed_operation_rejects_an_invalid_success_contract(
    operation: Callable[[TicklyApiClient], Awaitable[object]],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"unexpected": "raw-token"})

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(handler, operation)

    assert calls == 1
    assert raised.value.code == "upstream_contract_error"
    assert "raw-token" not in str(raised.value)
    # Pydantic 错误可能携带原始响应字段和值，不得成为公开异常的上下文。
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_task_response_datetimes_must_include_timezone() -> None:
    payload = task_payload()
    payload["created_at"] = "2026-08-19T01:02:03"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(McpToolError) as raised:
        await call_with_transport(
            handler,
            lambda client: client.create_task(
                token=TOKEN,
                request_id=REQUEST_ID,
                payload={"title": "任务", "topic": "工作"},
            ),
        )

    assert raised.value.code == "upstream_contract_error"
