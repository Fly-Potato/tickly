"""MCP HTTP 入口与工具协议层执行的安全中间件。"""

from collections.abc import Awaitable, Callable, Mapping
import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult
from pydantic import ValidationError
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth import token_from_authorization
from app.errors import McpToolError


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
PUBLIC_PATHS = frozenset({"/health", "/ready"})
LOG_PATHS = frozenset({"/mcp", "/health", "/ready"})
TOOL_NAMES = frozenset(
    {
        "list_tasks",
        "get_task",
        "list_topics",
        "find_parent_tasks",
        "create_task",
        "update_task",
        "set_task_status",
    }
)
access_logger = logging.getLogger("tickly.mcp.access")
tool_logger = logging.getLogger("tickly.mcp.tool")


class RequestIdMiddleware:
    """规范请求 ID，并让 SDK 与响应观察到同一个值。"""

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get(self.header_name)
        request_id = (
            supplied
            if supplied and REQUEST_ID_PATTERN.fullmatch(supplied)
            else str(uuid4())
        )
        # 协议 request ID 可以由客户端指定并继续回传/透传；日志关联 ID 必须
        # 始终由服务端生成，避免 Token、哈希或任务文本借此进入日志。
        log_request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        scope["state"]["log_request_id"] = log_request_id
        started = perf_counter()
        response_status = 500

        # SDK Context.headers 直接读取 ASGI scope；必须在进入 SDK 前移除所有旧值，
        # 再写入唯一的规范值，避免响应头与工具实际透传的 request ID 分叉。
        header_key = self.header_name.lower().encode("latin-1")
        scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() != header_key
        ]
        scope["headers"].append((header_key, request_id.encode("latin-1")))

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                MutableHeaders(scope=message)[self.header_name] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # 访问日志只读取 ASGI 元数据，不读取 query、header 或 body。
            access_logger.info(
                "request.completed",
                extra={
                    "request_id": log_request_id,
                    "method": scope["method"],
                    "path": scope["path"] if scope["path"] in LOG_PATHS else "other",
                    "status": response_status,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )


TokenVerifier = Callable[[str, str], Awaitable[None]]


async def _auth_response(scope: Scope, receive: Receive, send: Send) -> None:
    """发送不包含失败细节的固定 Bearer challenge。"""
    response = JSONResponse(
        {"error": "authentication_required"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )
    await response(scope, receive, send)


class ApiBearerMiddleware:
    """在协议解析前把 Bearer 交给 API 权威验证，且不缓存认证结果。

    明文 PAT 只保留在当前 ASGI request scope；共享 middleware 与 lifecycle
    不保存当前用户，避免并发请求互相污染。`/health` 与 `/ready` 保持公开。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: TokenVerifier,
        request_id_header: str,
    ) -> None:
        self.app = app
        self.verifier = verifier
        self.request_id_header = request_id_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = token_from_authorization(headers.get("Authorization"))
        request_id = headers.get(self.request_id_header)
        if token is None or request_id is None:
            await _auth_response(scope, receive, send)
            return

        try:
            await self.verifier(token, request_id)
        except McpToolError as error:
            # 上游异常对象可能持有敏感上下文；只读取固定错误码并返回固定正文。
            if error.code == "authentication_required":
                await _auth_response(scope, receive, send)
            else:
                response = JSONResponse(
                    {"error": "upstream_unavailable"},
                    status_code=503,
                )
                await response(scope, receive, send)
            return

        # 后续工具只可从本次已通过 API 验证的请求上下文取得明文凭据。
        scope.setdefault("state", {})["mcp_token"] = token
        await self.app(scope, receive, send)


def _tool_log_request_id(context: object) -> str | None:
    """只读取服务端日志关联 ID，不读取客户端协议 request ID 或 header。"""
    request = getattr(context, "request", None)
    scope = getattr(request, "scope", None)
    state = scope.get("state") if isinstance(scope, Mapping) else None
    request_id = state.get("log_request_id") if isinstance(state, Mapping) else None
    if isinstance(request_id, str) and REQUEST_ID_PATTERN.fullmatch(request_id):
        return request_id
    return None


def _tool_name(context: object) -> str:
    """工具名也按固定集合投影，未知客户端输入不得作为日志字段。"""
    params = getattr(context, "params", None)
    name = params.get("name") if isinstance(params, Mapping) else None
    return name if isinstance(name, str) and name in TOOL_NAMES else "unknown"


def _is_tool_error(result: HandlerResult) -> bool:
    """只读取协议错误布尔值，不检查可能含任务正文的 content。"""
    if isinstance(result, CallToolResult):
        return result.is_error is True
    return isinstance(result, Mapping) and result.get("isError") is True


class ToolLoggingMiddleware:
    """为 tools/call 记录一次完成事件，且不持有或输出调用参数。

    日志只包含固定工具名、结果、耗时、稳定错误码和服务端日志关联 ID。异常
    类型、repr、协议 content、arguments 与 Authorization 均不进入 LogRecord，
    从源头避免下游 formatter 或 caplog 意外持有敏感数据。
    """

    async def __call__(
        self,
        context: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if context.method != "tools/call":
            return await call_next(context)

        started = perf_counter()
        tool = _tool_name(context)
        request_id = _tool_log_request_id(context)
        try:
            result = await call_next(context)
        except ValidationError:
            self._log(
                request_id=request_id,
                tool=tool,
                started=started,
                outcome="error",
                error_code="validation_error",
            )
            raise
        except Exception:
            # 异常对象可能保留请求、响应或凭据，日志边界不得引用它。
            self._log(
                request_id=request_id,
                tool=tool,
                started=started,
                outcome="error",
                error_code="internal_error",
            )
            raise

        is_error = _is_tool_error(result)
        self._log(
            request_id=request_id,
            tool=tool,
            started=started,
            outcome="error" if is_error else "success",
            error_code="tool_error" if is_error else None,
        )
        return result

    @staticmethod
    def _log(
        *,
        request_id: str | None,
        tool: str,
        started: float,
        outcome: str,
        error_code: str | None,
    ) -> None:
        """构造固定白名单字段；缺失 request ID 时不伪造跨层关联。"""
        extra: dict[str, object] = {
            "tool": tool,
            "outcome": outcome,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
        }
        if request_id is not None:
            extra["request_id"] = request_id
        if error_code is not None:
            extra["error_code"] = error_code
        tool_logger.info("tool.completed", extra=extra)
