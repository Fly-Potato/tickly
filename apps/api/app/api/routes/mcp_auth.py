"""MCP transport 使用的内部认证探测契约。"""

from fastapi import APIRouter, status

from app.api.mcp_dependencies import McpCurrentUser


router = APIRouter(prefix="/internal/mcp/v1/auth", include_in_schema=False)


@router.post("/verify", status_code=status.HTTP_204_NO_CONTENT)
def verify(user: McpCurrentUser) -> None:
    """只确认本次 transport 认证，不授权后续资源请求。

    每个资源请求仍必须独立执行 MCP Token 依赖，避免 Token 在 transport 验证
    成功后、资源读取前被撤销时出现 check-and-use 权限窗口。
    """

    del user
