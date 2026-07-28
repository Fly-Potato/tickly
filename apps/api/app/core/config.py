from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


API_ROOT = Path(__file__).resolve().parents[2]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
DEFAULT_DEVELOPMENT_JWT_SECRET = "development-only-change-me"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TICKLY_",
        env_file=API_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "Tickly API"
    api_v1_prefix: str = "/api/v1"
    log_level: LogLevel = "INFO"
    log_json: bool = False
    request_id_header: str = "X-Request-ID"
    database_url: str = "sqlite:///./data/tickly.db"
    jwt_secret: str = DEFAULT_DEVELOPMENT_JWT_SECRET
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "tickly-api"
    jwt_audience: str = "tickly-web"
    access_token_minutes: PositiveInt = 15
    refresh_token_days: PositiveInt = 30
    refresh_cookie_name: str = "tickly_refresh"
    refresh_cookie_secure: bool = False

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        if value == "/" or not value.startswith("/") or value.endswith("/"):
            raise ValueError(
                "api_v1_prefix must start with '/' and must not end with '/'"
            )
        return value

    @model_validator(mode="after")
    def validate_production_authentication(self) -> "Settings":
        if self.environment is not Environment.PRODUCTION:
            return self

        # 生产环境必须显式注入安全配置，不能静默使用本地开发默认值。
        if (
            self.jwt_secret == DEFAULT_DEVELOPMENT_JWT_SECRET
            or len(self.jwt_secret) < 32
        ):
            raise ValueError("production jwt_secret must contain at least 32 characters")
        if not self.refresh_cookie_secure:
            raise ValueError("production refresh cookie must enable Secure")
        return self
