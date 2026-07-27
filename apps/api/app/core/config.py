from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


API_ROOT = Path(__file__).resolve().parents[2]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        if value == "/" or not value.startswith("/") or value.endswith("/"):
            raise ValueError(
                "api_v1_prefix must start with '/' and must not end with '/'"
            )
        return value
