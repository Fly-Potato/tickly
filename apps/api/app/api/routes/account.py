"""当前认证账号的自助维护 HTTP 契约。"""

import logging

from fastapi import APIRouter, Request, Response, status

from app.api.auth_cookies import delete_refresh_cookie
from app.api.dependencies import CurrentUser, DbSession
from app.core.errors import AppError, log_request_id_from
from app.schemas.account import PasswordChangeRequest
from app.services.accounts import (
    InvalidCurrentPassword,
    PasswordChangeFailed,
    PasswordUnchanged,
    change_own_password,
)


router = APIRouter(prefix="/account", tags=["account"])
logger = logging.getLogger("tickly.account")


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> None:
    """修改当前 access JWT 所属账号的密码并清除当前浏览器 refresh cookie。"""

    failure_stage = None
    try:
        change_own_password(
            session,
            user,
            payload.current_password,
            payload.new_password,
        )
    except InvalidCurrentPassword as error:
        raise AppError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_current_password",
            message="当前密码错误",
        ) from error
    except PasswordUnchanged as error:
        raise AppError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="password_unchanged",
            message="新密码不能与当前密码相同",
        ) from error
    except PasswordChangeFailed as error:
        failure_stage = error.stage

    if failure_stage is not None:
        # 只记录固定事件与安全维度，不附带异常对象、SQL、散列或请求值。
        logger.error(
            "account.password_change.failed",
            extra={
                "request_id": log_request_id_from(request),
                "user_id": user.id,
                "stage": failure_stage,
            },
        )
        raise AppError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="服务器内部错误",
        )

    # 数据库事务已撤销全部 refresh 会话；响应同时移除当前浏览器持有的副本。
    delete_refresh_cookie(response, request.app.state.settings)
