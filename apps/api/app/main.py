from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import Engine

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.config import API_ROOT, Settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import create_engine_for_settings
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
        if app.state.owns_database_engine:
            # 应用只释放自己创建的 Engine，避免关闭调用方注入的测试资源。
            app.state.database_engine.dispose()
        logger.info("application.stopped")


def create_app(
    settings: Settings | None = None,
    *,
    database_engine: Engine | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings)
    resolved_database_engine = database_engine or create_engine_for_settings(
        resolved_settings
    )
    application = FastAPI(
        title=resolved_settings.app_name,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.ready = False
    application.state.database_engine = resolved_database_engine
    application.state.owns_database_engine = database_engine is None
    application.state.alembic_config = Config(str(API_ROOT / "alembic.ini"))
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
