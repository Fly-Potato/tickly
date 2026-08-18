"""MCP 安全结构化日志的字段白名单与敏感信息回归测试。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter

from app.config import Environment, Settings
from app.logging import JsonFormatter, TextFormatter, configure_logging
from app.main import create_http_app
from app.middleware import ToolLoggingMiddleware
from app.schemas import TopicListPayload


RAW_TOKEN = "logging-raw-token"
TOKEN_SHA256 = hashlib.sha256(RAW_TOKEN.encode("utf-8")).hexdigest()
SENSITIVE_TEXT = "PRIVATE-TASK-BODY-XYZ"
INTERNAL_URL = "http://api:8321/internal/mcp/v1/tasks"


def make_settings(**overrides: Any) -> Settings:
    """构造不读取开发环境文件的最小 HTTP 测试配置。"""
    values: dict[str, Any] = {
        "environment": Environment.TEST,
        "token_sha256": TOKEN_SHA256,
        "allowed_hosts": ["testserver"],
        "allowed_origins": ["https://codex.example"],
        "api_base_url": "http://api:8321",
        "log_level": "INFO",
        "log_json": True,
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


def access_record() -> logging.LogRecord:
    """构造同时携带允许字段和攻击者可控敏感字段的日志记录。"""
    record = logging.LogRecord(
        name="tickly.mcp.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="request.completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "request-1"
    record.method = "POST"
    record.path = "/mcp"
    record.status = 200
    record.duration_ms = 1.25
    record.authorization = f"Bearer {RAW_TOKEN}"
    record.token_sha256 = TOKEN_SHA256
    record.body = SENSITIVE_TEXT
    record.detail = INTERNAL_URL
    return record


def test_json_log_contains_only_safe_access_fields() -> None:
    """JSON formatter 只能投影固定公共字段，任意 extra 都不得穿透。"""
    payload = json.loads(JsonFormatter().format(access_record()))

    assert set(payload) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "request_id",
        "method",
        "path",
        "status",
        "duration_ms",
    }
    assert payload["timestamp"].endswith("+00:00")
    assert payload["message"] == "request.completed"
    assert payload["request_id"] == "request-1"
    assert payload["duration_ms"] == 1.25
    rendered = json.dumps(payload, ensure_ascii=False)
    assert RAW_TOKEN not in rendered
    assert TOKEN_SHA256 not in rendered
    assert SENSITIVE_TEXT not in rendered
    assert INTERNAL_URL not in rendered


def test_text_log_contains_only_safe_fields_and_ignores_exception_detail() -> None:
    """文本 formatter 也必须走同一白名单，不能格式化异常正文或栈。"""
    record = access_record()
    try:
        raise RuntimeError(f"{SENSITIVE_TEXT} {INTERNAL_URL}")
    except RuntimeError:
        record.exc_info = sys.exc_info()

    rendered = TextFormatter().format(record)

    assert "message=request.completed" in rendered
    assert "request_id=request-1" in rendered
    assert "method=POST" in rendered
    assert "path=/mcp" in rendered
    assert "status=200" in rendered
    assert "duration_ms=1.25" in rendered
    assert RAW_TOKEN not in rendered
    assert TOKEN_SHA256 not in rendered
    assert SENSITIVE_TEXT not in rendered
    assert INTERNAL_URL not in rendered
    assert "RuntimeError" not in rendered


def test_logging_configuration_applies_level_and_selected_formatter() -> None:
    """应用配置必须控制 root 级别，并在 JSON/文本 formatter 间明确切换。"""
    configure_logging(make_settings(log_level="ERROR", log_json=False))
    root_logger = logging.getLogger()
    managed_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, "_tickly_mcp_handler", False)
    ]

    assert root_logger.level == logging.ERROR
    assert len(managed_handlers) == 1
    assert isinstance(managed_handlers[0].formatter, TextFormatter)

    configure_logging(make_settings(log_level="INFO", log_json=True))
    managed_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, "_tickly_mcp_handler", False)
    ]
    assert root_logger.level == logging.INFO
    assert len(managed_handlers) == 1
    assert isinstance(managed_handlers[0].formatter, JsonFormatter)


def _remove_managed_handlers() -> None:
    """移除绑定当前 pytest 捕获流的受管 handler，避免跨测试持有已关闭流。"""
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_tickly_mcp_handler", False):
            root_logger.removeHandler(handler)
            handler.close()


def test_logging_configuration_suppresses_uvicorn_access_query_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Uvicorn 默认 access handler 不得重复输出包含 query 的非 JSON 日志。"""
    uvicorn_access = logging.getLogger("uvicorn.access")
    previous_handlers = list(uvicorn_access.handlers)
    previous_level = uvicorn_access.level
    previous_propagate = uvicorn_access.propagate
    access_format = LOGGING_CONFIG["formatters"]["access"]["fmt"]
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(AccessFormatter(fmt=access_format))
    uvicorn_access.handlers = [handler]
    uvicorn_access.setLevel(logging.INFO)
    uvicorn_access.propagate = False

    try:
        configure_logging(make_settings(log_level="INFO", log_json=True))
        uvicorn_access.info(
            '%s - "%s %s HTTP/%s" %d',
            "testclient:1234",
            "POST",
            f"/mcp?detail={SENSITIVE_TEXT}&token_sha256={TOKEN_SHA256}",
            "1.1",
            200,
        )
        logging.getLogger("tickly.mcp.access").info(
            "request.completed",
            extra={
                "request_id": "uvicorn-access-request-1",
                "method": "POST",
                "path": "/mcp",
                "status": 200,
                "duration_ms": 1.25,
            },
        )
        rendered = capsys.readouterr().out
    finally:
        uvicorn_access.handlers = previous_handlers
        uvicorn_access.setLevel(previous_level)
        uvicorn_access.propagate = previous_propagate
        _remove_managed_handlers()

    assert SENSITIVE_TEXT not in rendered
    assert TOKEN_SHA256 not in rendered
    lines = [line for line in rendered.splitlines() if line]
    assert lines
    payloads = [json.loads(line) for line in lines]
    assert any(payload.get("message") == "request.completed" for payload in payloads)


@pytest.mark.asyncio
async def test_tool_log_records_only_allowlisted_identity_and_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """工具成功事件不得复制完整 arguments、Authorization 或任务字段。"""
    middleware = ToolLoggingMiddleware()
    context = SimpleNamespace(
        method="tools/call",
        params={
            "name": "create_task",
            "arguments": {
                "task": {
                    "title": SENSITIVE_TEXT,
                    "description": SENSITIVE_TEXT,
                },
                "token_sha256": TOKEN_SHA256,
            },
        },
        request=SimpleNamespace(
            scope={
                "state": {
                    "request_id": RAW_TOKEN,
                    "log_request_id": "8f288b43-3c92-4422-85a7-e55cba8f5833",
                }
            },
            headers={"Authorization": f"Bearer {RAW_TOKEN}"},
        ),
    )

    async def call_next(received: object) -> dict[str, object]:
        assert received is context
        return {"content": [], "isError": False}

    with caplog.at_level(logging.INFO, logger="tickly.mcp.tool"):
        result = await middleware(context, call_next)

    assert result == {"content": [], "isError": False}
    records = [
        record
        for record in caplog.records
        if record.name == "tickly.mcp.tool"
        and record.getMessage() == "tool.completed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.request_id == "8f288b43-3c92-4422-85a7-e55cba8f5833"
    assert record.tool == "create_task"
    assert record.outcome == "success"
    assert hasattr(record, "duration_ms")
    assert not hasattr(record, "arguments")
    assert not hasattr(record, "authorization")
    assert not hasattr(record, "token_sha256")
    assert RAW_TOKEN not in caplog.text
    assert TOKEN_SHA256 not in caplog.text
    assert SENSITIVE_TEXT not in caplog.text


@pytest.mark.asyncio
async def test_tool_log_sanitizes_unknown_name_and_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """异常事件只记录固定错误码，未知工具名和异常 repr 都视为不可信输入。"""
    middleware = ToolLoggingMiddleware()
    context = SimpleNamespace(
        method="tools/call",
        params={
            "name": SENSITIVE_TEXT,
            "arguments": {
                "detail": INTERNAL_URL,
                "token_sha256": TOKEN_SHA256,
            },
        },
        request=SimpleNamespace(
            scope={
                "state": {
                    "request_id": TOKEN_SHA256,
                    "log_request_id": "53ac6d4e-e117-46a8-889c-ffbc21ab849a",
                }
            }
        ),
    )

    async def call_next(received: object) -> dict[str, object]:
        assert received is context
        raise RuntimeError(
            f"{RAW_TOKEN} {TOKEN_SHA256} {SENSITIVE_TEXT} {INTERNAL_URL}"
        )

    with caplog.at_level(logging.INFO, logger="tickly.mcp.tool"):
        with pytest.raises(RuntimeError):
            await middleware(context, call_next)

    records = [
        record
        for record in caplog.records
        if record.name == "tickly.mcp.tool"
        and record.getMessage() == "tool.completed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.request_id == "53ac6d4e-e117-46a8-889c-ffbc21ab849a"
    assert record.tool == "unknown"
    assert record.outcome == "error"
    assert record.error_code == "internal_error"
    assert not hasattr(record, "exception")
    assert not hasattr(record, "detail")
    assert not hasattr(record, "token_sha256")
    assert RAW_TOKEN not in caplog.text
    assert TOKEN_SHA256 not in caplog.text
    assert SENSITIVE_TEXT not in caplog.text
    assert INTERNAL_URL not in caplog.text
    assert "RuntimeError" not in caplog.text


def test_real_http_access_log_excludes_authorization_body_and_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """真实 ASGI 请求只记录规范 path，不记录 query、Bearer 或请求正文。"""
    from starlette.testclient import TestClient

    application = create_http_app(make_settings())
    with caplog.at_level(logging.INFO, logger="tickly.mcp.access"):
        with TestClient(application) as client:
            response = client.post(
                f"/mcp?detail={SENSITIVE_TEXT}",
                headers={"Authorization": f"Bearer wrong-{RAW_TOKEN}"},
                json={"detail": SENSITIVE_TEXT},
            )

    assert response.status_code == 401
    records = [
        record
        for record in caplog.records
        if record.name == "tickly.mcp.access"
        and record.getMessage() == "request.completed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.method == "POST"
    assert record.path == "/mcp"
    assert record.status == 401
    assert record.request_id != response.headers["X-Request-ID"]
    UUID(record.request_id)
    assert RAW_TOKEN not in caplog.text
    assert TOKEN_SHA256 not in caplog.text
    assert SENSITIVE_TEXT not in caplog.text


@pytest.mark.asyncio
async def test_managed_access_output_sanitizes_client_ids_paths_and_concurrency(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """并发请求各用独立日志 ID，客户端 ID 与未知 path 不得进入真实输出。"""
    application = create_http_app(make_settings())
    transport = httpx2.ASGITransport(app=application)
    supplied_ids = (RAW_TOKEN, TOKEN_SHA256)

    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {RAW_TOKEN}"},
    ) as client:
        responses = await asyncio.gather(
            *(
                client.get(
                    f"/{SENSITIVE_TEXT}",
                    headers={"X-Request-ID": supplied_id},
                )
                for supplied_id in supplied_ids
            )
        )

    assert [response.headers["X-Request-ID"] for response in responses] == list(
        supplied_ids
    )
    rendered = capsys.readouterr().out
    _remove_managed_handlers()
    assert RAW_TOKEN not in rendered
    assert TOKEN_SHA256 not in rendered
    assert SENSITIVE_TEXT not in rendered
    payloads = [json.loads(line) for line in rendered.splitlines() if line]
    access_payloads = [
        payload for payload in payloads if payload.get("message") == "request.completed"
    ]
    assert len(access_payloads) == 2
    assert {payload["path"] for payload in access_payloads} == {"other"}
    log_request_ids = [payload["request_id"] for payload in access_payloads]
    assert len(set(log_request_ids)) == 2
    for log_request_id in log_request_ids:
        UUID(log_request_id)


class FakeApiClient:
    """为真实协议探针返回固定主题，只记录非敏感 request ID。"""

    def __init__(self) -> None:
        self.request_ids: list[str] = []

    async def list_topics(self, **values: object) -> TopicListPayload:
        request_id = values["request_id"]
        assert isinstance(request_id, str)
        self.request_ids.append(request_id)
        return TopicListPayload(items=["工作"])


@pytest.mark.asyncio
async def test_real_http_tool_log_reuses_access_request_id_without_body_leak(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """工具与访问事件共享服务端日志 ID，但上游仍收到客户端 request ID。"""
    settings = make_settings()
    fake = FakeApiClient()
    application = create_http_app(
        settings,
        api_client_override=fake,  # type: ignore[arg-type]
    )
    protocol_app = application.app.app  # type: ignore[attr-defined]
    transport = httpx2.ASGITransport(app=application)

    with caplog.at_level(logging.INFO, logger="tickly.mcp"):
        async with protocol_app.router.lifespan_context(protocol_app):
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={
                    "Authorization": f"Bearer {RAW_TOKEN}",
                    "X-Request-ID": RAW_TOKEN,
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
    tool_records = [
        record
        for record in caplog.records
        if record.name == "tickly.mcp.tool"
        and record.getMessage() == "tool.completed"
    ]
    assert len(tool_records) == 1
    UUID(tool_records[0].request_id)
    assert tool_records[0].request_id != RAW_TOKEN
    assert tool_records[0].tool == "list_topics"
    assert tool_records[0].outcome == "success"
    matching_access_records = [
        record
        for record in caplog.records
        if record.name == "tickly.mcp.access"
        and record.getMessage() == "request.completed"
        and getattr(record, "request_id", None) == tool_records[0].request_id
    ]
    assert matching_access_records
    assert all(record.path == "/mcp" for record in matching_access_records)
    assert fake.request_ids == [RAW_TOKEN]

    rendered = capsys.readouterr().out
    _remove_managed_handlers()
    assert RAW_TOKEN not in rendered
    assert TOKEN_SHA256 not in rendered
    assert SENSITIVE_TEXT not in rendered
    assert INTERNAL_URL not in rendered
    payloads = [json.loads(line) for line in rendered.splitlines() if line]
    tool_payloads = [
        payload for payload in payloads if payload.get("message") == "tool.completed"
    ]
    assert len(tool_payloads) == 1
    assert tool_payloads[0]["request_id"] == tool_records[0].request_id
    assert RAW_TOKEN not in caplog.text
    assert TOKEN_SHA256 not in caplog.text
    assert SENSITIVE_TEXT not in caplog.text
    assert INTERNAL_URL not in caplog.text
