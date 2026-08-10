import argparse
from collections.abc import Sequence

import uvicorn

from app.core.config import Settings


def run_server(*, reload: bool = False) -> None:
    """读取统一配置并启动 API 服务器。

    监听配置在打开网络端口前完成校验；reload 只由本地开发命令开启，
    生产容器必须保持单进程、无文件监视的启动方式。
    """
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=str(settings.host),
        port=settings.port,
        reload=reload,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动 Tickly API")
    parser.add_argument("--reload", action="store_true", help="启用开发热重载")
    arguments = parser.parse_args(argv)
    run_server(reload=arguments.reload)


if __name__ == "__main__":
    main()
