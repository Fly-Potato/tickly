"""Tickly MCP 仅输出固定事件与白名单字段的日志配置。"""

from datetime import datetime, timezone
import json
import logging
import re
import sys
from typing import Any

from app.config import Settings


SAFE_EXTRA_FIELDS = (
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "tool",
    "outcome",
    "error_code",
)
SAFE_EVENT_NAMES = frozenset({"request.completed", "tool.completed"})
_SAFE_TEXT_VALUE = re.compile(r"^[A-Za-z0-9._:/-]+$")


def _safe_message(record: logging.LogRecord) -> str:
    """只接受代码内声明的稳定事件名，禁止动态消息把敏感值带入日志。"""
    if isinstance(record.msg, str) and not record.args and record.msg in SAFE_EVENT_NAMES:
        return record.msg
    return "log.event"


def _payload(record: logging.LogRecord) -> dict[str, Any]:
    """投影日志白名单；异常对象、请求正文和任意 extra 永不进入输出。"""
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": _safe_message(record),
    }
    for field in SAFE_EXTRA_FIELDS:
        if hasattr(record, field):
            payload[field] = getattr(record, field)
    return payload


class JsonFormatter(logging.Formatter):
    """把安全字段输出为单行 UTF-8 JSON，不格式化异常或任意 extra。"""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_payload(record), ensure_ascii=False, separators=(",", ":"))


def _text_value(value: object) -> str:
    """让文本日志保持单行；含空白或控制字符的值使用 JSON 转义。"""
    if isinstance(value, str):
        return value if _SAFE_TEXT_VALUE.fullmatch(value) else json.dumps(value, ensure_ascii=False)
    return str(value)


class TextFormatter(logging.Formatter):
    """文本模式与 JSON 模式共享同一字段白名单。"""

    def format(self, record: logging.LogRecord) -> str:
        return " ".join(
            f"{key}={_text_value(value)}" for key, value in _payload(record).items()
        )


class _TicklyMcpFilter(logging.Filter):
    """只让本应用的安全事件进入受管 handler，隔离 SDK/HTTP 客户端日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("tickly.mcp.")


def configure_logging(settings: Settings) -> None:
    """按配置安装唯一 MCP handler，同时保留测试或宿主已安装的 handler。

    这里只管理带私有标记的 handler，不清空 root logger，避免应用工厂破坏
    pytest caplog 或 Uvicorn 的生命周期。HTTP 客户端降到 WARNING，防止其 INFO
    访问事件输出内部 API URL；业务诊断统一由本模块的白名单事件负责。
    """
    root_logger = logging.getLogger()
    for existing in list(root_logger.handlers):
        if getattr(existing, "_tickly_mcp_handler", False):
            root_logger.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler._tickly_mcp_handler = True  # type: ignore[attr-defined]
    handler.addFilter(_TicklyMcpFilter())
    handler.setFormatter(JsonFormatter() if settings.log_json else TextFormatter())
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)
    # Uvicorn 默认 access formatter 会输出完整 query；本应用已经在 ASGI 外层
    # 记录不含 query 的 request.completed，必须关闭其 INFO 事件以免重复和泄漏。
    for noisy_logger in ("httpx", "httpx2", "httpcore", "mcp", "uvicorn.access"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
