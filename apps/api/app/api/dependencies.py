"""FastAPI 请求级依赖。"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
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
        try:
            session.rollback()
        except SQLAlchemyError:
            # cleanup 数据库异常不能替换已经净化的请求异常，也不得进入日志。
            _invalidate_failed_session(session)
        raise
    finally:
        try:
            session.close()
        except SQLAlchemyError:
            # close 失败同样只废弃连接；未知编程错误仍由调用栈正常暴露。
            _invalidate_failed_session(session)


def _invalidate_failed_session(session: Session) -> None:
    """尽力废弃 cleanup 失败的连接，且不传播可能含 SQL 参数的数据库异常。"""

    try:
        session.invalidate()
    except SQLAlchemyError:
        # invalidate 也是数据库 cleanup；失败时不能反向污染原始安全响应。
        pass


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
