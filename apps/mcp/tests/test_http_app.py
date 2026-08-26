"""MCP Streamable HTTP 入口与生命周期测试。"""

from collections.abc import AsyncIterator, Callable
import logging
import re
from typing import Any

import httpx
from mcp.server import MCPServer
import pytest
from starlette.testclient import TestClient

from app.config import Environment, Settings
from app.errors import McpToolError
from app.main import create_http_app, create_mcp_server
from app.middleware import ApiBearerMiddleware, RequestIdMiddleware


RAW_TOKEN = "tickly-secret"
AUTH_HEADERS = {"Authorization": f"Bearer {RAW_TOKEN}"}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": Environment.TEST,
        "allowed_hosts": ["testserver"],
        "allowed_origins": ["https://codex.example"],
        "api_base_url": "http://api:8321",
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


class FakeApiClient:
    """只实现传输认证所需接口，避免协议解析测试访问真实网络。"""

    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []
        self.error: McpToolError | None = None

    async def verify_token(self, *, token: str, request_id: str) -> None:
        self.verifications.append((token, request_id))
        if self.error is not None:
            raise self.error
        if token != RAW_TOKEN:
            raise McpToolError("authentication_required", "需要 MCP 认证")


class TrackingReadyStream(httpx.AsyncByteStream):
    """记录 readiness 是否错误读取了不受信任的巨大正文。"""

    def __init__(self) -> None:
        self.read_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read_count += 1
        yield b"x" * 1_048_576

    async def aclose(self) -> None:
        self.closed = True


def make_app(*, fake: FakeApiClient | None = None):
    """创建带逐请求 API 验证替身的 HTTP 应用。"""
    return create_http_app(
        make_settings(),
        api_client_override=fake or FakeApiClient(),  # type: ignore[arg-type]
    )


def install_upstream(
    monkeypatch: Any,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[list[dict[str, Any]], list[httpx.AsyncClient]]:
    """替换真实传输，同时保留 AsyncClient 的超时与关闭行为。"""
    real_async_client = httpx.AsyncClient
    calls: list[dict[str, Any]] = []
    clients: list[httpx.AsyncClient] = []

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        calls.append(dict(kwargs))
        client = real_async_client(
            *args,
            **kwargs,
            transport=httpx.MockTransport(handler),
        )
        clients.append(client)
        return client

    monkeypatch.setattr("app.main.httpx.AsyncClient", factory)
    return calls, clients


def test_mcp_endpoint_rejects_missing_wrong_scheme_and_wrong_bearer() -> None:
    fake = FakeApiClient()
    with TestClient(make_app(fake=fake)) as client:
        responses = [
            client.post("/mcp", json={}),
            client.post(
                "/mcp", headers={"Authorization": "Basic wrong"}, json={}
            ),
            client.post(
                "/mcp", headers={"Authorization": "Bearer wrong"}, json={}
            ),
        ]

    assert {response.status_code for response in responses} == {401}
    for response in responses:
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json() == {"error": "authentication_required"}
        assert "wrong" not in response.text
    assert len(fake.verifications) == 1
    assert fake.verifications[0][0] == "wrong"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credential",
    [
        b"contains\x00nul",
        b"contains\x7fdel",
        b"contains\xffnon-ascii",
        b"x" * 513,
    ],
    ids=["nul", "del", "non-ascii", "overlong"],
)
async def test_malformed_raw_bearer_is_rejected_before_verifier_and_logging(
    credential: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """原始 ASGI header 中的危险字节不得进入 verifier、HTTPX 或日志。"""
    verifications: list[tuple[str, str]] = []
    inner_calls = 0

    async def verifier(token: str, request_id: str) -> None:
        verifications.append((token, request_id))

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        del scope, receive, send
        nonlocal inner_calls
        inner_calls += 1

    application = RequestIdMiddleware(
        ApiBearerMiddleware(
            inner,
            verifier=verifier,
            request_id_header="X-Request-ID",
        )
    )
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer " + credential)],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    with caplog.at_level(logging.INFO, logger="tickly.mcp.access"):
        await application(scope, receive, send)

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert start["status"] == 401
    assert body == b'{"error":"authentication_required"}'
    assert verifications == []
    assert inner_calls == 0
    assert credential not in body
    assert credential.decode("latin-1") not in caplog.text


def test_unauthenticated_invalid_input_does_not_reveal_protocol_details() -> None:
    with TestClient(make_app()) as client:
        response = client.post(
            "/mcp",
            headers={"Host": "evil.example", "Origin": "https://evil.example"},
            content=b"not-json",
        )

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_valid_bearer_reaches_mcp_protocol_layer() -> None:
    with TestClient(make_app()) as client:
        response = client.post("/mcp", headers=AUTH_HEADERS, json={})

    assert response.status_code != 401
    assert response.headers["X-Request-ID"]


def test_authenticated_invalid_json_does_not_accumulate_sdk_server_instances() -> None:
    application = make_app()
    protocol_app = application.app.app
    session_manager = protocol_app.routes[0].endpoint.session_manager

    with TestClient(application) as client:
        for _ in range(3):
            response = client.post(
                "/mcp",
                headers={**AUTH_HEADERS, "Content-Type": "application/json"},
                content=b"not-json",
            )
            assert response.status_code == 400

        # 无效请求不能留下只能等进程退出才清理的有状态 SDK transport。
        assert session_manager._server_instances == {}


def test_authenticated_request_rejects_invalid_host() -> None:
    with TestClient(make_app()) as client:
        response = client.post(
            "/mcp",
            headers={**AUTH_HEADERS, "Host": "evil.example"},
            json={},
        )

    assert response.status_code == 421


def test_authenticated_request_rejects_invalid_origin() -> None:
    with TestClient(make_app()) as client:
        response = client.post(
            "/mcp",
            headers={**AUTH_HEADERS, "Origin": "https://evil.example"},
            json={},
        )

    assert response.status_code == 403


def test_health_is_public_but_not_an_mcp_protocol_route() -> None:
    with TestClient(create_http_app(make_settings())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_ready_checks_upstream_without_forwarding_mcp_token(monkeypatch: Any) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ready"})

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        response = client.get(
            "/ready",
            headers={**AUTH_HEADERS, "X-Request-ID": "ready-check"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["X-Request-ID"] == "ready-check"
    assert len(requests) == 1
    assert requests[0].url.path == "/ready"
    assert "Authorization" not in requests[0].headers


def test_ready_never_reads_upstream_body_and_closes_stream(monkeypatch: Any) -> None:
    """就绪探针只信任状态码，不读取、解压或缓冲上游任意正文。"""
    stream = TrackingReadyStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert stream.read_count == 0
    assert stream.closed is True


def test_ready_returns_503_when_api_is_unreachable(monkeypatch: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("内部地址不得泄漏", request=request)

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "内部地址" not in response.text


def test_ready_returns_503_when_upstream_is_not_ready(monkeypatch: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "not_ready"})

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_ready_no_longer_depends_on_legacy_static_digest(monkeypatch: Any) -> None:
    """就绪探针只检查生命周期 client 与 API，不依赖旧静态摘要。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ready"})

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert [request.url.path for request in requests] == ["/ready"]


def test_lifespan_configures_timeouts_and_closes_http_client(monkeypatch: Any) -> None:
    calls, clients = install_upstream(
        monkeypatch,
        lambda request: httpx.Response(200, json={"status": "ready"}),
    )
    settings = make_settings(
        connect_timeout_seconds=2.5,
        request_timeout_seconds=7.5,
    )

    with TestClient(create_http_app(settings)) as client:
        assert clients and clients[0].is_closed is False
        assert client.get("/ready").status_code == 200

    assert clients[0].is_closed is True
    timeout = calls[0]["timeout"]
    assert timeout.connect == 2.5
    assert timeout.read == timeout.write == timeout.pool == 7.5
    assert str(calls[0]["base_url"]).rstrip("/") == "http://api:8321"


def test_request_id_is_returned_on_authentication_failure() -> None:
    with TestClient(make_app()) as client:
        response = client.post(
            "/mcp", headers={"X-Request-ID": "mcp-request-1"}, json={}
        )

    assert response.headers["X-Request-ID"] == "mcp-request-1"


def test_invalid_request_id_is_replaced_and_rewritten_for_inner_app() -> None:
    observed_headers: dict[str, str] = {}

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            # TestClient 会驱动完整 ASGI 生命周期，stub 必须确认启动和关闭。
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return
        observed_headers.update(
            {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in scope["headers"]
            }
        )
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    with TestClient(RequestIdMiddleware(inner)) as client:
        response = client.get("/", headers={"X-Request-ID": "contains space"})

    generated = response.headers["X-Request-ID"]
    assert generated != "contains space"
    assert REQUEST_ID_PATTERN.fullmatch(generated)
    assert observed_headers["x-request-id"] == generated


def test_request_id_middleware_wraps_api_authentication() -> None:
    """ASGI 包装顺序必须先规范 request ID，再调用 API 认证。"""
    application = make_app()

    assert isinstance(application, RequestIdMiddleware)
    assert isinstance(application.app, ApiBearerMiddleware)


def test_mcp_transport_validates_each_bearer_through_api(monkeypatch: Any) -> None:
    """每个 Bearer 都经真实 HTTP client 验证，结果不在网关缓存。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == f"Bearer {RAW_TOKEN}":
            return httpx.Response(204)
        return httpx.Response(
            401,
            json={"error": {"code": "authentication_required"}},
        )

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        invalid = client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong"},
            json={},
        )
        first = client.post(
            "/mcp",
            headers={**AUTH_HEADERS, "X-Request-ID": "verify-first"},
            json={},
        )
        second = client.post(
            "/mcp",
            headers={**AUTH_HEADERS, "X-Request-ID": "verify-second"},
            json={},
        )

    assert invalid.status_code == 401
    assert first.status_code != 401
    assert second.status_code != 401
    assert [request.url.path for request in requests] == [
        "/internal/mcp/v1/auth/verify",
        "/internal/mcp/v1/auth/verify",
        "/internal/mcp/v1/auth/verify",
    ]
    assert [request.headers["X-Request-ID"] for request in requests] == [
        invalid.headers["X-Request-ID"],
        "verify-first",
        "verify-second",
    ]


@pytest.mark.parametrize(
    ("handler", "expected_status"),
    [
        (
            lambda request: httpx.Response(
                503,
                json={"error": {"code": "database_busy", "detail": RAW_TOKEN}},
            ),
            503,
        ),
        (lambda request: httpx.Response(200, json={"token": RAW_TOKEN}), 503),
        (lambda request: httpx.Response(204, content=RAW_TOKEN.encode()), 503),
    ],
    ids=["upstream-5xx", "unexpected-200", "non-empty-204"],
)
def test_upstream_failures_return_fixed_503_without_credentials(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
    handler: Callable[[httpx.Request], httpx.Response],
    expected_status: int,
) -> None:
    """非认证类上游失败统一映射 503，响应和日志不得回显凭据。"""
    caplog.set_level(logging.INFO)
    install_upstream(monkeypatch, handler)

    with TestClient(create_http_app(make_settings())) as client:
        response = client.post("/mcp", headers=AUTH_HEADERS, json={})

    assert response.status_code == expected_status
    assert response.json() == {"error": "upstream_unavailable"}
    assert RAW_TOKEN not in response.text
    assert RAW_TOKEN not in caplog.text


def test_upstream_network_failure_returns_fixed_503(monkeypatch: Any) -> None:
    """网络异常不能在网关响应中暴露内部异常文本。"""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"内部地址与 {RAW_TOKEN} 不得泄漏",
            request=request,
        )

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        response = client.post("/mcp", headers=AUTH_HEADERS, json={})

    assert response.status_code == 503
    assert response.json() == {"error": "upstream_unavailable"}
    assert RAW_TOKEN not in response.text
    assert "内部地址" not in response.text


@pytest.mark.asyncio
async def test_lifecycle_client_not_started_returns_sanitized_503() -> None:
    """未进入 lifespan 时认证必须失败关闭，且不能访问未就绪 client。"""
    application = create_http_app(make_settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post("/mcp", headers=AUTH_HEADERS, json={})

    assert response.status_code == 503
    assert response.json() == {"error": "upstream_unavailable"}


def test_public_probes_do_not_trigger_token_verification() -> None:
    """健康与就绪探针保持公开，不能把请求 Bearer 交给验证器。"""
    fake = FakeApiClient()
    with TestClient(make_app(fake=fake)) as client:
        health = client.get("/health", headers=AUTH_HEADERS)

    assert health.status_code == 200
    assert fake.verifications == []


def test_mcp_server_metadata_and_instructions_are_explicit() -> None:
    server = create_mcp_server(make_settings())

    assert isinstance(server, MCPServer)
    assert server.name == "tickly"
    assert server.title == "Tickly Todo"
    assert server.description == "读取和管理 Tickly Todo"
    assert "不提供删除能力" in (server.instructions or "")
