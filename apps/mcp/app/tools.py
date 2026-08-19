"""把 Tickly 内部 API 映射为受限的 MCP 工具。"""

from collections.abc import Callable, Mapping
from typing import Any, cast

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ValidationError

from app.api_client import TicklyApiClient
from app.auth import token_from_authorization
from app.errors import McpToolError
from app.middleware import REQUEST_ID_PATTERN
from app.schemas import (
    CreateTaskArguments,
    CreateTaskInput,
    Cursor,
    FindParentTasksArguments,
    GetTaskArguments,
    ListTasksArguments,
    ListTopicsArguments,
    PageLimit,
    ParentOptionResult,
    ParentQuery,
    SetTaskStatusArguments,
    SortOrder,
    TaskDetailResult,
    TaskListResult,
    TaskSerial,
    TaskSort,
    TaskStatus,
    TaskStatusFilter,
    TaskWriteResult,
    TopicFilter,
    TopicListResult,
    UpdateTaskArguments,
    UpdateTaskInput,
)


SecurityContextProvider = Callable[[Context[Any]], tuple[str, str]]
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
_AUTHENTICATION_ERROR = ("authentication_required", "需要 MCP 认证")
_UPSTREAM_ERROR = ("upstream_unavailable", "Tickly API 暂时不可用")
_VALIDATION_ERROR_TEXT = "validation_error: 请求参数无效"
_TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "list_tasks": ListTasksArguments,
    "get_task": GetTaskArguments,
    "list_topics": ListTopicsArguments,
    "find_parent_tasks": FindParentTasksArguments,
    "create_task": CreateTaskArguments,
    "update_task": UpdateTaskArguments,
    "set_task_status": SetTaskStatusArguments,
}


def _validation_error_result() -> CallToolResult:
    """构造不携带字段位置、原始输入或动态模型名的固定工具错误。"""
    return CallToolResult(
        content=[TextContent(type="text", text=_VALIDATION_ERROR_TEXT)],
        is_error=True,
    )


def _publish_strict_tool_schemas(result: HandlerResult) -> HandlerResult:
    """让 tools/list 发布与本地安全预校验完全相同的封闭根 Schema。"""
    if not isinstance(result, dict):
        return result
    tools = result.get("tools")
    if not isinstance(tools, list):
        return result

    rewritten_tools: list[object] = []
    for tool in tools:
        if not isinstance(tool, dict):
            rewritten_tools.append(tool)
            continue
        model = _TOOL_ARGUMENT_MODELS.get(tool.get("name"))
        if model is None:
            rewritten_tools.append(tool)
            continue
        rewritten_tool = dict(tool)
        rewritten_tool["inputSchema"] = model.model_json_schema()
        rewritten_tools.append(rewritten_tool)
    return {**result, "tools": rewritten_tools}


async def safe_tool_validation_middleware(
    context: ServerRequestContext[Any, Any],
    call_next: CallNext,
) -> HandlerResult:
    """在 SDK 动态参数模型前执行封闭校验，并固定化本地校验错误。

    MCP SDK 2.0.0 的公开 tool decorator 会用 ``extra=ignore`` 的动态根模型，
    且默认错误会拼接 Pydantic ``ValidationError``。公共 Server middleware 是
    SDK 提供的参数验证前入口；这里不记录异常，也不把原始 arguments 保存在
    错误对象中，从而避免任务正文或凭据随校验详情返回客户端。
    """
    if context.method == "tools/call" and isinstance(context.params, Mapping):
        tool_name = context.params.get("name")
        model = _TOOL_ARGUMENT_MODELS.get(tool_name) if isinstance(tool_name, str) else None
        if model is not None:
            arguments = context.params.get("arguments")
            try:
                model.model_validate({} if arguments is None else arguments)
            except ValidationError:
                return _validation_error_result()

    result = await call_next(context)
    if context.method == "tools/list":
        return _publish_strict_tool_schemas(result)
    return result


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
    """注册当前阶段唯一允许的七个工具，且不提供删除能力。

    每次调用都从当前请求取得安全上下文，并复用生命周期中的
    ``TicklyApiClient``。闭包不缓存明文 Token，避免凭据跨请求或进入进程状态。
    """
    server.middleware.append(safe_tool_validation_middleware)

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

    @server.tool(annotations=WRITE, structured_output=True)
    async def create_task(
        task: CreateTaskInput,
        ctx: Context[Any],
    ) -> TaskWriteResult:
        """创建根任务或以账号流水号引用父任务的一层子任务。

        只转发调用方明确提供的字段；默认值、父级解析和业务规范化继续由
        Tickly API 权威处理，MCP 层不得猜测缺失内容。
        """
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).create_task(
            token=token,
            request_id=request_id,
            payload=task.model_dump(exclude_unset=True, mode="json"),
        )
        return TaskWriteResult(summary=f"已创建任务 #{payload.serial}", task=payload)

    @server.tool(annotations=WRITE, structured_output=True)
    async def update_task(
        serial: TaskSerial,
        patch: UpdateTaskInput,
        ctx: Context[Any],
    ) -> TaskWriteResult:
        """按账号流水号 patch 普通字段，不在此入口修改任务状态。

        ``exclude_unset`` 保留 PATCH 的省略/显式 null 区别，避免把未提供字段
        意外清空；父任务关系仍由内部 API 在事务内校验。
        """
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).update_task(
            token=token,
            request_id=request_id,
            serial=serial,
            patch=patch.model_dump(exclude_unset=True, mode="json"),
        )
        return TaskWriteResult(summary=f"已更新任务 #{serial}", task=payload)

    @server.tool(annotations=WRITE, structured_output=True)
    async def set_task_status(
        serial: TaskSerial,
        status: TaskStatus,
        ctx: Context[Any],
    ) -> TaskWriteResult:
        """把任务切换为 New、In Progress 或 Completed。"""
        token, request_id = security_context_provider(ctx)
        payload = await _api_client_from(ctx).update_task(
            token=token,
            request_id=request_id,
            serial=serial,
            patch={"status": status.value},
        )
        return TaskWriteResult(summary=f"已更新任务 #{serial} 的状态", task=payload)
