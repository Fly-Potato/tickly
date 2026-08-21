# Tickly VPS 部署说明

本文说明如何在单台 VPS 上部署 Tickly 的 API、MCP 和 Web 服务。线上使用 GHCR 预构建镜像，Traefik 负责公网 HTTPS，Web/Caddy 负责同源路由。

## 部署边界

- VPS 只运行一套 API、MCP、Web 容器；SQLite 数据由 API 独占。
- API 与 MCP 不发布宿主机端口，只有 Web 通过 Traefik 接入公网。
- Traefik 必须已经运行，并创建好外部 Docker 网络、HTTPS entrypoint 和证书 resolver。
- 生产部署使用明确的 `v*` 或 `sha-*` 镜像标签，不使用会漂移的 `latest`。

## VPS 准备

安装 Docker Engine 和 Compose plugin，把仓库中的 `compose.yaml`、`compose.traefik.yaml` 及部署所需文件放到 VPS。确认 Traefik 外部网络存在：

```bash
docker network inspect traefik
```

若实际网络名称不同，后续 `.env` 中的 `TICKLY_TRAEFIK_NETWORK` 必须使用实际名称。VPS 只需要拉取镜像，不需要源码构建。

## 配置服务器环境变量

在仓库根目录创建仅限服务器读取的 `.env`，至少包含：

```dotenv
TICKLY_IMAGE_TAG=sha-<已验证提交>
TICKLY_DOMAIN=todo.example.com
TICKLY_TRAEFIK_NETWORK=traefik
TICKLY_TRAEFIK_ENTRYPOINT=websecure
TICKLY_TRAEFIK_CERT_RESOLVER=letsencrypt

TICKLY_JWT_SECRET=<至少 32 个随机字符>
TICKLY_MCP_TOKEN_SHA256=<原始 MCP Token 的小写 SHA-256>
TICKLY_MCP_ALLOWED_HOSTS=["todo.example.com"]
TICKLY_MCP_ALLOWED_ORIGINS=["https://todo.example.com"]
```

原始 MCP Token 只保存在调用客户端的安全环境中，不能写入 VPS `.env`、日志或仓库。`TICKLY_MCP_TOKEN_SHA256` 必须同时提供给 API 和 MCP；生产环境缺少或格式非法时，MCP 会拒绝启动。

## 首次部署

先确认三个镜像使用同一个已验证标签，再拉取并检查合并后的 Compose 配置：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml config --quiet
docker compose -f compose.yaml -f compose.traefik.yaml pull
```

首次启动或升级前，先执行数据库 migration，再启动服务：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml run --rm api python -m alembic upgrade head
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
docker compose -f compose.yaml -f compose.traefik.yaml ps
```

首次部署还需要创建唯一账号：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml run --rm api \
  python -m app.cli user create --username <用户名>
```

## 线上验收

将域名替换为 `.env` 中的 `TICKLY_DOMAIN`：

```bash
curl --fail https://todo.example.com/health
curl --fail https://todo.example.com/ready
curl --fail -i https://todo.example.com/internal/mcp/v1/tasks
curl --fail -i https://todo.example.com/mcp
```

验收结果应满足：

- 首页和 SPA 深链接可访问。
- `/health` 返回 API 存活状态，`/ready` 同时确认数据库和 migration。
- `/internal/*` 固定返回 `404`。
- 未携带 Bearer Token 的 `/mcp` 被拒绝。
- 错误 Host 返回 `421`，错误 Origin 返回 `403`。
- `docker compose ... ps` 中 API、MCP、Web 均为 healthy，且宿主机没有发布 `8080`、`8321` 或 `8322`。

真实 HTTPS、认证和 MCP 工具调用 smoke 必须在目标 VPS 完成；本地 HTTP Compose 检查不能替代线上验收。

## 升级、备份与回滚

升级前记录当前镜像标签、三个镜像 digest、migration revision，并备份 `tickly-data` volume。SQLite 数据备份和恢复必须单独验证，不能用重启容器代替数据恢复。

升级时修改 `.env` 的 `TICKLY_IMAGE_TAG`，然后重复拉取、migration 和启动：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml pull
docker compose -f compose.yaml -f compose.traefik.yaml run --rm api python -m alembic upgrade head
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
```

应用 smoke 失败时，先回退到上一已知健康的 `v*` 或 `sha-*` 标签。若新版本包含不可逆 migration，必须从升级前备份恢复数据库，不要执行未经验证的 downgrade。

## Token 轮换

生成新的原始 Token，计算其摘要，更新 VPS `.env` 的 `TICKLY_MCP_TOKEN_SHA256`，再重建 API/MCP：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml up --detach --force-recreate api mcp
docker compose -f compose.yaml -f compose.traefik.yaml ps
```

服务不支持新旧 Token 并存；切换摘要后旧 Token 会立即失效，应预留短暂连接中断。

## 故障排查

- MCP 启动失败：查看 `docker compose ... logs mcp`，优先检查 Token 摘要、Host/Origin 白名单和 API 依赖是否可达。
- MCP `/ready` 返回 `503`：检查 API `/ready`、MCP HTTP client 生命周期和数据库 migration。
- `/mcp` 返回 `421` 或 `403`：分别检查请求 Host 与 Origin 是否匹配 `.env` 白名单。
- 工具返回 `mcp_account_unavailable`：确认数据库中恰好存在一个启用账号。
- 容器反复退出：核对 VPS 是否使用了同一组镜像标签、`.env` 是否被 Compose 读取，以及 migration 是否在启动前完成。
