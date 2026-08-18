"""把 Tickly 内部 API 映射为受限的 MCP 工具。"""

from collections.abc import Callable, Mapping
from typing import Any, cast

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

from app.api_client import TicklyApiClient
from app.auth import token_from_authorization
from app.errors import McpToolError
from app.middleware import REQUEST_ID_PATTERN
from app.schemas import (
    Cursor,
    PageLimit,
    ParentOptionResult,
    ParentQuery,
    SortOrder,
    TaskDetailResult,
    TaskListResult,
    TaskSerial,
    TaskSort,
    TaskStatusFilter,
    TopicFilter,
    TopicListResult,
)


SecurityContextProvider = Callable[[Context[Any]], tuple[str, str]]
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_AUTHENTICATION_ERROR = ("authentication_required", "需要 MCP 认证")
_UPSTREAM_ERROR = ("upstream_unavailable", "Tickly API 暂时不可用")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """兼容普通映射与 ASGI Headers，同时不复制或记录敏感 header。"""
    direct = headers.get(name)
    if direct is not None:
        return direct
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def request_security_context(
    context: Context[Any],
    request_id_header: str = "X-Request-ID",
) -> tuple[str, str]:
    """从入口已验证并规范化的请求头取得明文凭据与 request ID。

    该函数不是独立认证器：Bearer 哈希校验由外层中间件完成。这里仍对缺失、
    非 HTTP 上下文和异常 request ID 失败关闭，且错误对象不保留原始 header。
    """
    headers = context.headers
    if headers is None:
        raise McpToolError(*_AUTHENTICATION_ERROR) from None
    token = token_from_authorization(_header(headers, "Authorization"))
    request_id = _header(headers, request_id_header)
    if (
        token is None
        or request_id is None
        or REQUEST_ID_PATTERN.fullmatch(request_id) is None
    ):
        raise McpToolError(*_AUTHENTICATION_ERROR) from None
    return token, request_id


def _api_client_from(context: Context[Any]) -> TicklyApiClient:
    """只从当前 MCP 生命周期取 client，绝不自行连接数据库或新建连接池。"""
    try:
        api_client = context.request_context.lifespan_context.api_client
    except (AttributeError, ValueError):
        raise McpToolError(*_UPSTREAM_ERROR) from None
    return cast(TicklyApiClient, api_client)


def register_tools(
    server: MCPServer[Any],
    security_context_provider: SecurityContextProvider = request_security_context,
) -> None:
    """注册当前阶段唯一允许的四个只读工具。

    每次调用都从当前请求取得安全上下文，并复用生命周期中的
    ``TicklyApiClient``。闭包不缓存明文 Token，避免凭据跨请求或进入进程状态。
    """

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def list_tasks(
        ctx: Context[Any],
        status: TaskStatusFilter = "all",
        topic: TopicFilter = None,
        sort: TaskSort = "created_at",
        order: SortOrder = "desc",
        cursor: Cursor = None,
        limit: PageLimit = 50,
    ) -> TaskListResult:
        """按筛选和稳定 cursor 读取完整根任务组。"""
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).list_tasks(
            token=token,
            request_id=request_id,
            status=status,
            topic=topic,
            sort=sort,
            order=order,
            cursor=cursor,
            limit=limit,
        )
        return TaskListResult(
            summary=f"已读取 {len(payload.items)} 个任务组",
            items=payload.items,
            next_cursor=payload.next_cursor,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def get_task(
        serial: TaskSerial,
        ctx: Context[Any],
    ) -> TaskDetailResult:
        """按账号流水号读取一个任务及其直接子任务。"""
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).get_task(
            token=token,
            request_id=request_id,
            serial=serial,
        )
        return TaskDetailResult(
            summary=f"已读取任务 #{payload.serial}",
            task=payload,
            children=payload.children,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def list_topics(ctx: Context[Any]) -> TopicListResult:
        """读取当前账号实际存在的精确主题值。"""
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).list_topics(
            token=token,
            request_id=request_id,
        )
        return TopicListResult(
            summary=f"已读取 {len(payload.items)} 个主题",
            items=payload.items,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    async def find_parent_tasks(
        ctx: Context[Any],
        query: ParentQuery = None,
        cursor: Cursor = None,
        limit: PageLimit = 50,
    ) -> ParentOptionResult:
        """按标题或 ``#serial`` 查找可作为父任务的根任务。"""
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).find_parent_tasks(
            token=token,
            request_id=request_id,
            query=query,
            cursor=cursor,
            limit=limit,
        )
        return ParentOptionResult(
            summary=f"已读取 {len(payload.items)} 个父任务候选",
            items=payload.items,
            next_cursor=payload.next_cursor,
        )
