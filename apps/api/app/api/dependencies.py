"""FastAPI 请求级依赖。"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import User
from app.services.auth import AuthenticationRequired, authenticate_access_token


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """从当前应用实例获取 Session，确保测试和多实例不会串库。"""

    session = request.app.state.database_session_factory()
    try:
        yield session
    except Exception:
        # 请求内任意异常都必须回滚，避免未提交的写入污染连接后续请求。
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db_session)]


_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(_bearer),
]


def get_current_user(
    request: Request,
    session: DbSession,
    credentials: BearerCredentials,
) -> User:
    """把 Bearer token 解析为当前活跃用户，并隐藏具体失败原因。"""

    if credentials is None:
        raise _authentication_required()
    try:
        return authenticate_access_token(
            session,
            credentials.credentials,
            request.app.state.settings,
        )
    except AuthenticationRequired as error:
        raise _authentication_required() from error


def _authentication_required() -> AppError:
    return AppError(
        status_code=401,
        code="authentication_required",
        message="需要登录",
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
