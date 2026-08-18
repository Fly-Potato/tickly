from enum import StrEnum
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    IPvAnyAddress,
    UrlConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


MCP_ROOT = Path(__file__).resolve().parents[1]
Port = Annotated[int, Field(ge=1, le=65535)]
PositiveSeconds = Annotated[float, Field(gt=0, le=300)]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """集中校验 MCP 监听、上游 API 与传输安全配置。"""

    model_config = SettingsConfigDict(
        env_prefix="TICKLY_MCP_",
        env_file=MCP_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    host: IPvAnyAddress = IPv4Address("127.0.0.1")
    port: Port = 8322
    api_base_url: Annotated[
        AnyHttpUrl, UrlConstraints(preserve_empty_path=True)
    ] = Field(default="http://127.0.0.1:8321", validate_default=True)
    token_sha256: str | None = None
    allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*"]
    allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]
    connect_timeout_seconds: PositiveSeconds = 3
    request_timeout_seconds: PositiveSeconds = 15
    request_id_header: str = "X-Request-ID"
    log_level: LogLevel = "INFO"
    log_json: bool = False
    max_request_body_size: int = Field(default=1_048_576, ge=1024, le=4_194_304)

    @field_validator("token_sha256")
    @classmethod
    def validate_token_hash(cls, value: str | None) -> str | None:
        """只接受固定长度的小写摘要，避免不同 token 表示绕过比较边界。"""
        if value is None:
            return None
        if (
            len(value) != 64
            or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("token_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """生产环境必须显式配置鉴权摘要和传输白名单，缺失时拒绝启动。"""
        if self.environment is Environment.PRODUCTION:
            if self.token_sha256 is None:
                raise ValueError("production token_sha256 is required")
            if not self.allowed_hosts or not self.allowed_origins:
                raise ValueError("production transport allowlists are required")
        return self
