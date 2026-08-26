"""MCP Bearer Token 的严格解析边界。"""


MAX_BEARER_TOKEN_LENGTH = 512


def token_from_authorization(value: str | None) -> str | None:
    """解析单个安全 ASCII Bearer；危险字节不得进入 HTTP client。

    不在网关校验 PAT 业务格式，普通可打印 ASCII 仍交 API 权威判断；这里只
    限制 header 语法、控制字符和资源上限，避免 HTTPX 编码异常与超长转发。
    """
    if value is None:
        return None
    parts = value.split(" ")
    if len(parts) != 2:
        return None
    scheme, token = parts
    if (
        scheme.lower() != "bearer"
        or not 1 <= len(token) <= MAX_BEARER_TOKEN_LENGTH
        or any(not 0x21 <= ord(character) <= 0x7E for character in token)
    ):
        return None
    return token
