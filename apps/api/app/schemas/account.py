"""账号自助维护接口的请求 schema。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import MAX_PASSWORD_INPUT_LENGTH, validate_password


class PasswordChangeRequest(BaseModel):
    """仅接收当前密码和合规新密码，拒绝由客户端指定账号身份。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    current_password: Annotated[
        str,
        Field(max_length=MAX_PASSWORD_INPUT_LENGTH),
    ]
    new_password: Annotated[
        str,
        Field(max_length=MAX_PASSWORD_INPUT_LENGTH),
    ]

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)
