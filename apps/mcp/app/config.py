import re
from enum import StrEnum
from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

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
_HOSTNAME_PATTERN = re.compile(
    r"(?=.{1,253}\.?$)"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*\.?"
)


def _is_valid_transport_hostname(value: str) -> bool:
    """接受 IP literal 或符合 Host/Origin authority 约束的 DNS 主机名。"""
    # 远程 HTTPS MCP 不需要 link-local scope；如未来支持，需按 RFC 6874 独立实现。
    if "%" in value:
        return False
    try:
        ip_address(value)
    except ValueError:
        return _HOSTNAME_PATTERN.fullmatch(value) is not None
    return True


def _validate_host_allowlist_entry(value: str) -> None:
    """校验 SDK 支持的精确 Host 与尾部端口通配模式。"""
    # 解析器和 IP zone 语法都可能接受异常字符，入口统一限制为无空格的可打印 ASCII。
    if not value or any(
        ord(character) <= 32 or ord(character) >= 127 for character in value
    ):
        raise ValueError("allowed_hosts 项仅允许无空格的可打印 ASCII")
    if "?" in value or "#" in value:
        raise ValueError("allowed_hosts 项不得包含 ? 或 #")

    wildcard_port = value.endswith(":*")
    authority = value[:-2] if wildcard_port else value
    if "*" in authority or authority.endswith(":"):
        raise ValueError("allowed_hosts 项包含不受支持的通配模式")

    parsed = urlsplit(f"//{authority}")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("allowed_hosts 项必须使用有效端口") from error

    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or not _is_valid_transport_hostname(parsed.hostname)
        or (wildcard_port and port is not None)
    ):
        raise ValueError("allowed_hosts 项必须是 Host 请求头模式")


def _validate_origin_allowlist_entry(value: str) -> None:
    """校验 SDK 支持的精确 HTTP(S) Origin 与尾部端口通配模式。"""
    # 与 Host 使用同一输入不变量，确保 urlsplit 接收的原值不会被静默规范化。
    if not value or any(
        ord(character) <= 32 or ord(character) >= 127 for character in value
    ):
        raise ValueError("allowed_origins 项仅允许无空格的可打印 ASCII")
    if "?" in value or "#" in value:
        raise ValueError("allowed_origins 项不得包含 ? 或 #")

    wildcard_port = value.endswith(":*")
    origin = value[:-2] if wildcard_port else value
    if (
        "*" in origin
        or origin.endswith(":")
        or not origin.startswith(("http://", "https://"))
    ):
        raise ValueError("allowed_origins 项包含不受支持的模式")

    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("allowed_origins 项必须使用有效端口") from error

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or not _is_valid_transport_hostname(parsed.hostname)
        or (wildcard_port and port is not None)
    ):
        raise ValueError("allowed_origins 项必须是序列化 Origin")


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

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, values: list[str]) -> list[str]:
        """启动前拒绝 SDK 无法安全匹配的 Host 配置，避免运行期全部拒绝请求。"""
        for value in values:
            _validate_host_allowlist_entry(value)
        return values

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, values: list[str]) -> list[str]:
        """启动前拒绝非序列化 Origin，避免无效白名单造成服务不可用。"""
        for value in values:
            _validate_origin_allowlist_entry(value)
        return values

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
