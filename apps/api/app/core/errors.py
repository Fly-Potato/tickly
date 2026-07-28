import logging
import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger("tickly.errors")


class AppError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers or {}


def request_id_from(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_content(request: Request, *, code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id_from(request), "details": details or []}}


def response_headers(request: Request, existing: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(existing or {})
    header_name = request.app.state.settings.request_id_header
    headers[header_name] = request_id_from(request)
    return headers


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_content(request, code=exc.code, message=exc.message, details=exc.details), headers=response_headers(request, exc.headers))


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"location": list(error["loc"]), "type": error["type"], "message": error["msg"]} for error in exc.errors()]
    return JSONResponse(status_code=422, content=error_content(request, code="validation_error", message="请求参数无效", details=details), headers=response_headers(request))


async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        code, message = "not_found", "资源不存在"
    else:
        code = "http_error"
        message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    return JSONResponse(status_code=exc.status_code, content=error_content(request, code=code, message=message), headers=response_headers(request, exc.headers))


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("request.failed", extra={"request_id": request_id_from(request)})
    return JSONResponse(status_code=500, content=error_content(request, code="internal_error", message="服务器内部错误"), headers=response_headers(request))


async def handle_database_operational_error(
    request: Request, exc: OperationalError
) -> JSONResponse:
    """仅把 SQLite 锁竞争映射为可重试错误，其余数据库异常继续隐藏细节。"""

    original = exc.orig
    message = str(original).lower()
    if isinstance(original, sqlite3.OperationalError) and (
        "database is locked" in message or "database table is locked" in message
    ):
        return JSONResponse(
            status_code=503,
            content=error_content(
                request,
                code="database_busy",
                message="数据库繁忙，请稍后重试",
            ),
            headers=response_headers(request),
        )

    # SQL、参数与底层异常文本不得进入响应，沿用统一的内部错误边界。
    return await handle_unexpected_error(request, exc)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(OperationalError, handle_database_operational_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
