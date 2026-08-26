"""浏览器 refresh cookie 的统一写入与删除边界。"""

from datetime import UTC, datetime

from fastapi import Response

from app.core.config import Settings


def set_refresh_cookie(
    response: Response,
    refresh_token: str,
    expires_at: datetime,
    settings: Settings,
) -> None:
    """设置仅供认证路由使用的 HttpOnly refresh cookie。"""

    # SQLite 读取 DateTime 时可能丢失 tzinfo；Cookie 过期时间必须明确按 UTC 解释。
    cookie_expiry = expires_at
    if getattr(cookie_expiry, "tzinfo", None) is None:
        cookie_expiry = cookie_expiry.replace(tzinfo=UTC)
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        expires=cookie_expiry,
        path=_refresh_cookie_path(settings),
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def delete_refresh_cookie(response: Response, settings: Settings) -> None:
    """用与写入时相同的属性清除浏览器 refresh cookie。"""

    # 路径与安全属性不一致会留下同名 Cookie，导致服务端已撤销但浏览器仍持有。
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=_refresh_cookie_path(settings),
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _refresh_cookie_path(settings: Settings) -> str:
    return f"{settings.api_v1_prefix}/auth"
