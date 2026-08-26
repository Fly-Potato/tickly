"""用户名登录与 JWT 会话 HTTP 契约。"""

from fastapi import APIRouter, Request, Response, status

from app.api.auth_cookies import delete_refresh_cookie, set_refresh_cookie
from app.api.dependencies import CurrentUser, DbSession
from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse
from app.services.auth import (
    InvalidCredentials,
    RefreshReplayed,
    RefreshRequired,
    login_user,
    logout_session,
    refresh_session,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> TokenResponse:
    settings = request.app.state.settings
    try:
        result = login_user(
            session,
            payload.username,
            payload.password,
            settings,
            user_agent=request.headers.get("user-agent"),
        )
    except InvalidCredentials as error:
        raise AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_credentials",
            message="用户名或密码错误",
        ) from error

    set_refresh_cookie(response, result.refresh_token, result.session.expires_at, settings)
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    session: DbSession,
) -> TokenResponse:
    settings = request.app.state.settings
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token is None:
        raise _refresh_error("refresh_required", "需要刷新登录", settings)

    try:
        result = refresh_session(session, refresh_token, settings)
    except RefreshRequired as error:
        raise _refresh_error("refresh_required", "需要刷新登录", settings) from error
    except RefreshReplayed as error:
        raise _refresh_error(
            "refresh_replayed", "登录会话已失效", settings
        ) from error

    set_refresh_cookie(response, result.refresh_token, result.session.expires_at, settings)
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: DbSession,
) -> None:
    settings = request.app.state.settings
    logout_session(
        session,
        request.cookies.get(settings.refresh_cookie_name),
        settings,
    )
    delete_refresh_cookie(response, settings)


@router.get("/me", response_model=CurrentUserResponse)
def me(user: CurrentUser) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        username=user.username,
        timezone=user.timezone,
        is_active=user.is_active,
    )


def _refresh_error(code: str, message: str, settings: Settings) -> AppError:
    deletion = Response()
    delete_refresh_cookie(deletion, settings)
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code=code,
        message=message,
        headers={"Set-Cookie": deletion.headers["set-cookie"]},
    )
