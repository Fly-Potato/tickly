"""MCP 静态 Bearer Token 的解析与常量时间校验。"""

import hashlib
import secrets


def bearer_matches(token: str, expected_sha256: str | None) -> bool:
    """只比较摘要，服务端不持有可用于鉴权的明文配置。"""
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return expected_sha256 is not None and secrets.compare_digest(
        actual, expected_sha256
    )


def token_from_authorization(value: str | None) -> str | None:
    """解析单个 Bearer 凭据；格式不完整时统一返回未认证。"""
    if value is None:
        return None
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token
