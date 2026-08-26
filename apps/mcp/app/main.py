"""Tickly MCPServer、Streamable HTTP 应用与上游生命周期。"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import partial

import httpx
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.api_client import TicklyApiClient
from app.config import Settings
from app.errors import McpToolError
from app.logging import configure_logging
from app.middleware import (
    ApiBearerMiddleware,
    RequestIdMiddleware,
    ToolLoggingMiddleware,
)
from app.tools import (
    SecurityContextProvider,
    register_tools,
    request_security_context,
)


INSTRUCTIONS = (
    "Tickly MCP 仅管理当前账号的 Todo。引用不明确时先调用只读工具确认 serial；"
    "写入必须遵循 Codex 审批策略。服务不提供删除能力，不得把内部 UUID 当作用户输入。"
)


@dataclass(frozen=True)
class AppContext:
    """一个 MCP 进程生命周期内共享的内部 API 边界。"""

    api_client: TicklyApiClient


@dataclass
class LifecycleState:
    """向 transport 认证与健康路由暴露同一生命周期 API client。"""

    http: httpx.AsyncClient | None = None
    api_client: TicklyApiClient | None = None


def build_lifespan(
    settings: Settings,
    api_client_override: TicklyApiClient | None = None,
    *,
    lifecycle_state: LifecycleState | None = None,
) -> Callable[
    [MCPServer[AppContext]], AbstractAsyncContextManager[AppContext]
]:
    """构建唯一 HTTPX client，并在 MCP 停止时确定性关闭连接池。"""
    state = lifecycle_state or LifecycleState()

    @asynccontextmanager
    async def lifespan(server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        del server
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.request_timeout_seconds,
            write=settings.request_timeout_seconds,
            pool=settings.request_timeout_seconds,
        )
        async with httpx.AsyncClient(
            base_url=str(settings.api_base_url),
            timeout=timeout,
        ) as http:
            api_client = api_client_override or TicklyApiClient(
                http,
                max_response_bytes=settings.max_request_body_size,
                request_id_header=settings.request_id_header,
            )
            state.http = http
            state.api_client = api_client
            try:
                yield AppContext(api_client=api_client)
            finally:
                # 先同时关闭认证与 readiness 入口，再由 context 释放连接池。
                state.api_client = None
                state.http = None

    return lifespan


def register_health_routes(
    server: MCPServer[AppContext],
    lifecycle_state: LifecycleState,
) -> None:
    """注册不经过 Bearer 的存活与依赖就绪探针。"""

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        del request
        return JSONResponse({"status": "ok"})

    @server.custom_route("/ready", methods=["GET"], include_in_schema=False)
    async def ready(request: Request) -> JSONResponse:
        del request
        http = lifecycle_state.http
        if http is None or lifecycle_state.api_client is None:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        try:
            # readiness 只需要状态码；使用 streaming context 确定性关闭响应，
            # 且不读取、解压或缓冲上游可控正文。
            async with http.stream("GET", "/ready") as response:
                upstream_status = response.status_code
        except (httpx.TimeoutException, httpx.RequestError):
            # HTTPX 异常可能持有内部 URL；探针只返回固定状态，不串联异常。
            return JSONResponse({"status": "not_ready"}, status_code=503)
        if upstream_status != 200:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ready"})


def create_mcp_server(
    settings: Settings,
    *,
    api_client_override: TicklyApiClient | None = None,
    security_context_provider: SecurityContextProvider | None = None,
    lifecycle_state: LifecycleState | None = None,
) -> MCPServer[AppContext]:
    """创建官方 SDK v2 MCPServer，并绑定 Tickly 单一上游生命周期。"""
    state = lifecycle_state or LifecycleState()
    server = MCPServer(
        name="tickly",
        title="Tickly Todo",
        description="读取和管理 Tickly Todo",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        log_level=settings.log_level,
        lifespan=build_lifespan(
            settings,
            api_client_override,
            lifecycle_state=state,
        ),
    )
    register_health_routes(server, state)
    # 工具日志必须位于参数安全校验外层，才能观察校验短路且绝不读取 arguments。
    server.middleware.append(ToolLoggingMiddleware())
    resolved_security_context_provider = security_context_provider or partial(
        request_security_context,
        request_id_header=settings.request_id_header,
    )
    register_tools(server, resolved_security_context_provider)
    return server


def create_http_app(
    settings: Settings,
    *,
    api_client_override: TicklyApiClient | None = None,
) -> ASGIApp:
    """把认证置于 SDK 外层，避免未认证请求获得协议解析细节。"""
    configure_logging(settings)
    lifecycle_state = LifecycleState()
    server = create_mcp_server(
        settings,
        api_client_override=api_client_override,
        lifecycle_state=lifecycle_state,
    )
    protocol_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        max_request_body_size=settings.max_request_body_size,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=settings.allowed_origins,
        ),
        host=str(settings.host),
    )

    async def verify_with_api(token: str, request_id: str) -> None:
        """只读取当前 active client；未启动或已关闭时固定失败关闭。"""
        api_client = lifecycle_state.api_client
        if api_client is None:
            raise McpToolError("upstream_unavailable", "Tickly API 暂时不可用")
        await api_client.verify_token(token=token, request_id=request_id)

    authenticated_app = ApiBearerMiddleware(
        protocol_app,
        verifier=verify_with_api,
        request_id_header=settings.request_id_header,
    )
    return RequestIdMiddleware(
        authenticated_app,
        header_name=settings.request_id_header,
    )


app = create_http_app(Settings())
