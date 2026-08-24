# API 容器自动数据库迁移设计

## 目标

Tickly 的 API 容器在默认启动时先执行 `python -m alembic upgrade head`，迁移成功后再启动 API 服务。更新镜像后执行常规 `docker compose up --detach` 即可完成迁移，不再要求运维人员单独运行 migration 命令。

## 范围

- 只修改 API 容器的默认启动路径、Compose 边界检查和部署文档。
- 本地 `mise exec -- pnpm dev:api`、测试进程及 FastAPI 应用生命周期不自动执行 migration。
- `docker compose run api <命令>` 覆盖默认命令时不自动迁移，避免 CLI、排障或 downgrade 命令被隐式改写。
- 不新增迁移锁、重试器或多实例协调机制；生产仍保持单 API 实例独占 SQLite。

## 启动流程

API 镜像包含一个最小启动脚本，Dockerfile 将它设为默认 `CMD`：

1. 使用与 API 相同的 `TICKLY_DATABASE_URL` 执行 `python -m alembic upgrade head`。
2. 迁移返回成功后，通过 `exec python -m app.server` 替换 shell 进程，保持容器信号和退出码语义。
3. 迁移失败时立即退出，不启动 API；依赖 API healthy 的 MCP 和 Web 因此不会启动为可用状态。

Alembic 通过 `alembic_version` 跳过已经应用的 revision，因此容器重启时重复运行 `upgrade head` 不会重复执行已完成的 DDL。

## 验证

- 先扩展 `scripts/check-compose.ps1`，要求 API Dockerfile 使用自动迁移启动脚本，并校验脚本按“迁移成功后 exec API”的顺序执行；在实现前确认检查失败。
- 验证基础 Compose 和 Traefik 合并配置仍满足现有网络、镜像和依赖边界。
- 构建 API 镜像，并在一次性 SQLite volume 上验证首次启动完成 migration、API ready，以及同一数据库上的第二次启动不会重复执行 revision。
- 验证迁移失败时 API 进程不会启动。
- 同步更新部署说明：保留升级前备份要求，删除首次部署和升级流程中的手动 migration 命令，明确自动迁移失败的排查方式。

## 回滚边界

镜像回滚不会自动执行 Alembic downgrade。若新 migration 不可逆或旧镜像无法读取新 schema，仍须从升级前备份恢复数据库；自动迁移不改变这一边界。
