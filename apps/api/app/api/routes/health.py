from fastapi import APIRouter, Request, status

from app.core.errors import AppError


router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="not_ready",
            message="服务尚未就绪",
        )
    return {"status": "ready"}
