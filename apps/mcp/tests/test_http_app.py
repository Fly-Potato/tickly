"""MCP Streamable HTTP 入口与生命周期测试。"""

from collections.abc import Callable
import hashlib
import re
from typing import Any

import httpx
from mcp.server import MCPServer
from starlette.testclient import TestClient

from app.config import Environment, Settings
from app.main import create_http_app, create_mcp_server
from app.middleware import RequestIdMiddleware


RAW_TOKEN = "tickly-secret"
TOKEN_SHA256 = hashlib.sha256(RAW_TOKEN.encode("utf-8")).hexdigest()
AUTH_HEADERS = {"Authorization": f"Bearer {RAW_TOKEN}"}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": Environment.TEST,
        "token_sha256": TOKEN_SHA256,
        "allowed_hosts": ["testserver"],
        "allowed_origins": ["https://codex.example"],
        "api_base_url": "http://api:8321",
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


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
    with TestClient(create_http_app(make_settings())) as client:
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


def test_unauthenticated_invalid_input_does_not_reveal_protocol_details() -> None:
    with TestClient(create_http_app(make_settings())) as client:
        response = client.post(
            "/mcp",
            headers={"Host": "evil.example", "Origin": "https://evil.example"},
            content=b"not-json",
        )

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_missing_token_configuration_fails_closed() -> None:
    settings = make_settings(token_sha256=None)

    with TestClient(create_http_app(settings)) as client:
        response = client.post("/mcp", headers=AUTH_HEADERS, json={})

    assert response.status_code == 401


def test_valid_bearer_reaches_mcp_protocol_layer() -> None:
    with TestClient(create_http_app(make_settings())) as client:
        response = client.post("/mcp", headers=AUTH_HEADERS, json={})

    assert response.status_code != 401
    assert response.headers["X-Request-ID"]


def test_authenticated_request_rejects_invalid_host() -> None:
    with TestClient(create_http_app(make_settings())) as client:
        response = client.post(
            "/mcp",
            headers={**AUTH_HEADERS, "Host": "evil.example"},
            json={},
        )

    assert response.status_code == 421


def test_authenticated_request_rejects_invalid_origin() -> None:
    with TestClient(create_http_app(make_settings())) as client:
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
    with TestClient(create_http_app(make_settings())) as client:
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


def test_mcp_server_metadata_and_instructions_are_explicit() -> None:
    server = create_mcp_server(make_settings())

    assert isinstance(server, MCPServer)
    assert server.name == "tickly"
    assert server.title == "Tickly Todo"
    assert server.description == "读取和管理 Tickly Todo"
    assert "不提供删除能力" in (server.instructions or "")
