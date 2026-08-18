"""MCP HTTP 入口在协议解析前执行的安全中间件。"""

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth import bearer_matches, token_from_authorization


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
PUBLIC_PATHS = frozenset({"/health", "/ready"})


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
        scope.setdefault("state", {})["request_id"] = request_id

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
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[self.header_name] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class StaticBearerMiddleware:
    """在任何 MCP 协议或传输错误可见前校验静态凭据。

    明文 Token 只保留在当前请求 header/state 中，既不写入配置，也不进入
    错误正文。`/health` 与 `/ready` 是容器探针，刻意保持公开。
    """

    def __init__(self, app: ASGIApp, *, expected_sha256: str | None) -> None:
        self.app = app
        self.expected_sha256 = expected_sha256

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        token = token_from_authorization(Headers(scope=scope).get("Authorization"))
        if token is None or not bearer_matches(token, self.expected_sha256):
            response = JSONResponse(
                {"error": "authentication_required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        # 后续工具只可从已通过本中间件的请求上下文取得明文凭据。
        scope.setdefault("state", {})["mcp_token"] = token
        await self.app(scope, receive, send)
