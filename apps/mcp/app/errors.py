"""MCP 工具可安全返回给调用方的稳定错误。"""


class McpToolError(Exception):
    """只携带公开错误码和固定消息，不保存上游 URL、正文或凭据。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.public_message = message
