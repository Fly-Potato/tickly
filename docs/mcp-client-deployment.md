# Tickly VPS 部署与运行维护

本文说明如何在单台 VPS 上部署和维护 Tickly 的 API、MCP 和 Web 服务。线上使用 GHCR 预构建镜像，Traefik 负责公网 HTTPS，Web/Caddy 负责同源路由。镜像发布规则见 [镜像发布说明](release.md)，MCP 客户端配置见 [MCP 客户端说明](mcp.md)。

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
TICKLY_MCP_ALLOWED_HOSTS=["todo.example.com"]
TICKLY_MCP_ALLOWED_ORIGINS=["https://todo.example.com"]
```

生产环境必须显式配置可信 Host 与 Origin 白名单。用户级原始 MCP Token 由 Web 设置页签发，只保存在调用客户端的安全环境中；不要把它写入 VPS `.env`、Compose 服务环境、日志或仓库。

## 首次部署

先确认三个镜像使用同一个已验证标签，再拉取并检查合并后的 Compose 配置：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml config --quiet
docker compose -f compose.yaml -f compose.traefik.yaml pull
```

首次启动或升级时，API 容器会先自动执行 `alembic upgrade head`，成功后才启动服务。迁移失败会使 API 容器退出，并阻止依赖它的 MCP 和 Web 进入可用状态：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
docker compose -f compose.yaml -f compose.traefik.yaml ps
```

确认 API、MCP、Web 均为 healthy 后，再创建所需账号。账号仍由管理员使用现有 CLI 创建，不提供 Web 注册；可以为不同用户名重复执行命令：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml run --rm api \
  python -m app.cli user create --username <用户名>
```

用户随后登录 `https://todo.example.com/settings`，为每台设备或用途创建具名 MCP Token。有效期可选 90 天、365 天（默认）或永不过期；原始值只显示一次，应立即保存到对应客户端的秘密存储并按 [MCP 客户端说明](mcp.md) 配置。一个用户可以同时持有多个 Token。

## 线上验收

将域名替换为 `.env` 中的 `TICKLY_DOMAIN`：

```bash
(
  set -eu
  base_url=https://todo.example.com
  body=$(mktemp)
  trap 'rm -f "$body"' EXIT

  curl --fail --silent --show-error "$base_url/health"
  curl --fail --silent --show-error "$base_url/ready"

  status=$(curl --silent --show-error --output "$body" --write-out '%{http_code}' \
    "$base_url/internal/mcp/v1/tasks")
  test "$status" = 404

  status=$(curl --silent --show-error --output "$body" --write-out '%{http_code}' \
    "$base_url/mcp")
  test "$status" = 401
  grep -Eq '"error"[[:space:]]*:[[:space:]]*"authentication_required"' "$body"
)
```

验收结果应满足：

- 首页和 SPA 深链接可访问。
- `/health` 返回 API 存活状态，`/ready` 同时确认数据库和 migration。
- `/internal/*` 固定返回 `404`。
- 未携带 Bearer Token 的 `/mcp` 返回 `401 authentication_required`。
- 错误 Host 返回 `421`，错误 Origin 返回 `403`。
- `docker compose ... ps` 中 API、MCP、Web 均为 healthy，且宿主机没有发布 `8080`、`8321` 或 `8322`。

真实 HTTPS、认证和 MCP 工具调用 smoke 必须在目标 VPS 完成；本地 HTTP Compose 检查不能替代线上验收。

## 升级、备份与回滚

升级前记录当前镜像标签、三个镜像 digest、migration revision，并备份 `tickly-data` volume。SQLite 数据备份和恢复必须单独验证，不能用重启容器代替数据恢复。

升级时只使用一组已经完成 API、MCP、Web 契约验证的相同标签，不要错开升级三个应用。修改 `.env` 的 `TICKLY_IMAGE_TAG`，然后拉取镜像并启动；API 容器会在对外提供服务前自动完成 migration，MCP 等待 API healthy，Web 等待 API 和 MCP healthy：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml pull
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
```

应用 smoke 失败时，不能只修改镜像标签就假设数据兼容。仅当上一版本与当前 schema 的兼容性已经验证时，才回退到上一已知健康的 `v*` 或 `sha-*` 标签；否则同时恢复升级前数据库备份和对应镜像。不要在线执行未经演练的 downgrade，新版本签发的 Token 和升级后的数据也不会存在于升级前备份中。

## 从旧共享凭据迁移

旧凭据无法安全归属到某个用户，也无法自动转换为个人 Token；新旧认证不提供双认证兼容期。应安排明确的 MCP 凭据轮换窗口，并按以下顺序升级：

1. 记录版本与 migration revision，完成可恢复的数据库备份。
2. 使用同一已验证标签部署 API、MCP、Web；由 API 先执行 migration，并等待三个服务 healthy。迁移保留已有用户、任务和 refresh 会话。旧 Web access token 因缺少版本声明会被 API 拒绝，但仍有效的旧 refresh session 可以自动轮换，并按当前 `auth_version` 签发新的 access token。只有 refresh 已撤销、过期、失效，或者用户改密、管理员改密或撤销会话时，才需要重新输入登录凭据。
3. 管理员按需用现有 CLI 创建额外账号；已有账号无需重建。
4. 用户访问 `/settings`；Web 能通过旧 refresh session 恢复认证时无需重新输入凭据，否则先登录。随后创建自己的具名 MCP Token，更新对应客户端的 `TICKLY_MCP_TOKEN` 并重新连接。
5. 所有客户端完成轮换后，从 VPS `.env`、Shell 和秘密管理清单中删除旧共享摘要配置。新 Compose 即使看到该旧环境值也不会把它传入 API 或 MCP。

新服务生效后，旧 MCP 凭据会立即停止工作；在用户创建并配置个人 Token 前存在预期的 MCP 连接中断。不要把这一步描述为无停机轮换，也不要回退到不一致的单个应用镜像。

## Token 轮换与账号维护

日常轮换可以利用每用户多 Token 能力：先在 `/settings` 创建替代 Token，更新并验证单个客户端，再撤销旧 Token。撤销按设备生效，不影响同一用户的其他 Token；原始值遗失时无法找回，只能重新签发。

用户在设置页修改 Web 密码会让旧 Web access/refresh 凭据失效，但不会自动撤销 MCP Token。需要使设备 Token 失效时必须单独撤销。管理员执行 `user deactivate` 后，该账号的 Web 登录、刷新和全部 MCP Token 都会停止工作。

## 故障排查

- MCP 启动失败：查看 `docker compose ... logs mcp`，优先检查 Host/Origin 白名单和 API 依赖是否可达。
- MCP `/ready` 返回 `503`：检查 API `/ready`、MCP HTTP client 生命周期和数据库 migration。
- `/mcp` 返回 `421` 或 `403`：分别检查请求 Host 与 Origin 是否匹配 `.env` 白名单。
- `/mcp` 返回 `401 authentication_required`：确认客户端使用实际 `/mcp` URL、启动进程已继承 `TICKLY_MCP_TOKEN`，并检查 Token 是否已撤销、到期或所属账号已停用；不要把原始 Token 写入命令行或日志。原始值遗失时在 `/settings` 重新签发。
- 传输连接成功但工具随后返回认证失败：Token 可能在传输验证后被撤销或到期；内部任务 API 会使用同一 Bearer 再验证一次，应更换有效 Token 后重连。
- API 容器退出：使用 `docker compose ... logs api` 检查自动 migration 错误；不要绕过失败迁移强制启动旧 schema 上的 API。
- 容器反复退出：核对 VPS 是否使用了同一组镜像标签、`.env` 是否被 Compose 读取，以及自动 migration 是否成功完成。
