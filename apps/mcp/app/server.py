"""Tickly MCP 的 Uvicorn 命令行入口。"""

import argparse
from collections.abc import Sequence

import uvicorn

from app.config import Settings


def run_server(*, reload: bool = False) -> None:
    """配置校验完成后启动统一 ASGI 应用。"""
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=str(settings.host),
        port=settings.port,
        reload=reload,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """只开放本地开发所需的 reload 开关，生产参数全部来自环境。"""
    parser = argparse.ArgumentParser(description="启动 Tickly MCP")
    parser.add_argument("--reload", action="store_true", help="启用开发热重载")
    arguments = parser.parse_args(argv)
    run_server(reload=arguments.reload)


if __name__ == "__main__":
    main()
