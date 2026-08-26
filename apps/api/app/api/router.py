from fastapi import APIRouter

from app.api.routes.account import router as account_router
from app.api.routes.auth import router as auth_router
from app.api.routes.mcp_tokens import router as mcp_tokens_router
from app.api.routes.tasks import router as tasks_router

api_router = APIRouter()
api_router.include_router(account_router)
api_router.include_router(auth_router)
api_router.include_router(mcp_tokens_router)
api_router.include_router(tasks_router)
