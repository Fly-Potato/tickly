"""MCP 调用 Tickly 内部 API 的唯一 HTTP 边界。"""

from collections.abc import Mapping
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.errors import McpToolError
from app.schemas import (
    ParentOptionPagePayload,
    TaskDetailPayload,
    TaskListPayload,
    TaskPayload,
    TopicListPayload,
)


PayloadModel = TypeVar("PayloadModel", bound=BaseModel)
_CONTRACT_ERROR = ("upstream_contract_error", "Tickly API 返回了无效响应")
_UNAVAILABLE_ERROR = ("upstream_unavailable", "Tickly API 暂时不可用")
_KNOWN_MESSAGES = {
    "authentication_required": "需要 MCP 认证",
    "mcp_account_unavailable": "MCP 账号不可用",
    "task_not_found": "任务不存在",
    "invalid_cursor": "分页游标无效",
    "invalid_task_relationship": "父待办关系无效",
    "validation_error": "请求参数无效",
}


class TicklyApiClient:
    """转发已验证凭据，并把所有上游失败收敛为固定公开错误。

    该 client 不读取数据库，也不实现重试。尤其是写请求超时可能发生在 API
    已提交事务之后，自动重试会造成重复创建或额外更新时间副作用。
    """

    def __init__(self, http: httpx.AsyncClient, *, max_response_bytes: int) -> None:
        self._http = http
        self._max_response_bytes = max_response_bytes

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        request_id: str,
        params: Mapping[str, object | None] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """只发起一次请求；底层错误、内部地址和敏感正文均不进入公开错误。"""
        query = (
            {key: value for key, value in params.items() if value is not None}
            if params is not None
            else None
        )
        response: httpx.Response | None = None
        try:
            response = await self._http.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-ID": request_id,
                },
                params=query,
                json=dict(json) if json is not None else None,
            )
        except (httpx.TimeoutException, httpx.RequestError):
            # HTTPX 异常持有完整 request（含 Authorization）；离开 except 后再抛
            # 固定错误，避免把含凭据的异常链接到 MCP 可见错误对象。
            pass

        if response is None:
            raise McpToolError(*_UNAVAILABLE_ERROR)

        if len(response.content) > self._max_response_bytes:
            raise McpToolError(*_CONTRACT_ERROR)
        return self._decode_response(response)

    def _decode_response(self, response: httpx.Response) -> dict[str, Any]:
        """仅允许稳定错误码，未知响应失败关闭且不回显上游内容。"""
        body: Any = None
        parsed = False
        try:
            body = response.json()
            parsed = True
        except ValueError:
            # 解码异常可能携带响应片段，固定错误不保留异常上下文。
            pass
        if not parsed or not isinstance(body, dict):
            raise McpToolError(*_CONTRACT_ERROR)
        if 200 <= response.status_code < 300:
            return body

        error_body = body.get("error")
        code = error_body.get("code") if isinstance(error_body, dict) else None
        if isinstance(code, str) and code in _KNOWN_MESSAGES:
            raise McpToolError(code, _KNOWN_MESSAGES[code])
        if response.status_code >= 500:
            raise McpToolError(*_UNAVAILABLE_ERROR)
        raise McpToolError(*_CONTRACT_ERROR)

    async def _validated_request(
        self,
        model: type[PayloadModel],
        method: str,
        path: str,
        *,
        token: str,
        request_id: str,
        params: Mapping[str, object | None] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> PayloadModel:
        body = await self._request(
            method,
            path,
            token=token,
            request_id=request_id,
            params=params,
            json=json,
        )
        validated: PayloadModel | None = None
        try:
            validated = model.model_validate(body)
        except ValidationError:
            # 校验详情可能含上游任务字段和值；离开 except 后抛出固定错误，
            # 不让原始响应成为公开异常的 cause/context。
            pass
        if validated is None:
            raise McpToolError(*_CONTRACT_ERROR)
        return validated

    async def list_tasks(
        self,
        *,
        token: str,
        request_id: str,
        status: str,
        topic: str | None,
        sort: str,
        order: str,
        cursor: str | None,
        limit: int,
    ) -> TaskListPayload:
        return await self._validated_request(
            TaskListPayload,
            "GET",
            "/internal/mcp/v1/tasks",
            token=token,
            request_id=request_id,
            params={
                "status": status,
                "topic": topic,
                "sort": sort,
                "order": order,
                "cursor": cursor,
                "limit": limit,
            },
        )

    async def get_task(
        self, *, token: str, request_id: str, serial: int
    ) -> TaskDetailPayload:
        return await self._validated_request(
            TaskDetailPayload,
            "GET",
            f"/internal/mcp/v1/tasks/{serial}",
            token=token,
            request_id=request_id,
        )

    async def list_topics(
        self, *, token: str, request_id: str
    ) -> TopicListPayload:
        return await self._validated_request(
            TopicListPayload,
            "GET",
            "/internal/mcp/v1/tasks/topics",
            token=token,
            request_id=request_id,
        )

    async def find_parent_tasks(
        self,
        *,
        token: str,
        request_id: str,
        query: str | None,
        cursor: str | None,
        limit: int,
    ) -> ParentOptionPagePayload:
        return await self._validated_request(
            ParentOptionPagePayload,
            "GET",
            "/internal/mcp/v1/tasks/parent-options",
            token=token,
            request_id=request_id,
            params={"query": query, "cursor": cursor, "limit": limit},
        )

    async def create_task(
        self,
        *,
        token: str,
        request_id: str,
        payload: Mapping[str, object],
    ) -> TaskPayload:
        return await self._validated_request(
            TaskPayload,
            "POST",
            "/internal/mcp/v1/tasks",
            token=token,
            request_id=request_id,
            json=payload,
        )

    async def update_task(
        self,
        *,
        token: str,
        request_id: str,
        serial: int,
        patch: Mapping[str, object],
    ) -> TaskPayload:
        return await self._validated_request(
            TaskPayload,
            "PATCH",
            f"/internal/mcp/v1/tasks/{serial}",
            token=token,
            request_id=request_id,
            json=patch,
        )
