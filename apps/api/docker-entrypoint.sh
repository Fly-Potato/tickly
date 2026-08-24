#!/bin/sh
set -eu

# 容器只在 migration 成功后启动 API；失败退出码直接交给编排系统处理。
python -m alembic upgrade head
exec python -m app.server
