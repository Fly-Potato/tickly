from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.config import Settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware.request_id import RequestIdMiddleware


logger = logging.getLogger("tickly.lifecycle")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ready = True
    logger.info("application.started")
    try:
        yield
    finally:
        app.state.ready = False
        logger.info("application.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings)
    application = FastAPI(
        title=resolved_settings.app_name,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.ready = False
    application.add_middleware(
        RequestIdMiddleware,
        header_name=resolved_settings.request_id_header,
    )
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(
        api_router,
        prefix=resolved_settings.api_v1_prefix,
    )
    return application


app = create_app()
