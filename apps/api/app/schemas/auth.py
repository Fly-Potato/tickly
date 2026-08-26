"""认证接口的请求与响应 schema。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.security import MAX_PASSWORD_INPUT_LENGTH, normalize_username


class LoginRequest(BaseModel):
    username: str
    password: Annotated[str, Field(max_length=MAX_PASSWORD_INPUT_LENGTH)]

    @field_validator("username")
    @classmethod
    def normalize_login_username(cls, value: str) -> str:
        return normalize_username(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class CurrentUserResponse(BaseModel):
    id: str
    username: str
    timezone: str
    is_active: bool
