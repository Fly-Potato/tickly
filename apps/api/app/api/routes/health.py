import logging

from fastapi import APIRouter, Request, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError, request_id_from
from app.db.readiness import database_migration_is_current


logger = logging.getLogger("tickly.readiness")
router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="not_ready",
            message="服务尚未就绪",
        )

    try:
        migration_is_current = database_migration_is_current(
            request.app.state.database_engine,
            request.app.state.alembic_config,
        )
    except SQLAlchemyError as exc:
        # 日志只记录稳定事件名和异常类型，避免泄漏连接 URL、路径或 SQL。
        logger.warning(
            "readiness.database_unavailable",
            extra={
                "request_id": request_id_from(request),
                "error_type": type(exc).__name__,
            },
        )
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="数据库不可用",
        ) from exc

    if not migration_is_current:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="migration_not_current",
            message="数据库迁移版本不是最新",
        )

    return {"status": "ready"}
