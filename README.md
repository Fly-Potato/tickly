# Tickly

Tickly 是一个计划接入 AI 能力的个人多设备 Todo 应用 monorepo。

- `apps/web`：React + Vite 前端
- `apps/api`：由 uv 管理的 FastAPI 后端
- `apps/mcp`：由 uv 管理的远程 MCP 服务
- `packages/*`：可复用 workspace 包
- `docs/roadmaps`：0 到 1 产品与工程路线图
- `docs/superpowers`：已确认的设计与实施计划

当前已完成工程与容器基线、SQLAlchemy/SQLite 持久化、单账号用户名 + JWT 认证闭环、受当前用户所有权保护的 Todo API、响应式 Todo Web，以及供 Codex 使用的远程 MCP 服务。Todo 支持账号内流水编号 `serial`、New / In Progress / Completed 三种状态、必填的自由文本主题、可选截止时间和一层父子待办。Web 支持快速新增、编辑、删除确认、筛选、排序、cursor 加载更多和账号 IANA 时区；桌面端为左侧筛选、右侧列表的两栏布局，移动端通过 Dialog 抽屉打开筛选。模型调用、自然语言任务草稿等 AI 能力尚未实现。账号只能通过后端 CLI 创建和维护。

数据库 schema 通过 Alembic 显式管理。API 本地默认使用 `apps/api/data/tickly.db`，首次运行前从仓库根目录执行：

```bash
mise exec -- uv --directory apps/api run alembic upgrade head
mise exec -- uv --directory apps/api run alembic current
```

回退本地 schema：

```bash
mise exec -- uv --directory apps/api run alembic downgrade base
```

应用启动不会自动创建或修改生产数据库 schema。
首份 migration 已直接采用 `username`。如果本地曾用旧版本首份 migration 创建过无须保留的数据库，应停止 API、删除 `apps/api/data/tickly.db`，再重新执行 `alembic upgrade head`；本项目不提供旧开发数据兼容 migration。

## 初始化

```bash
mise install
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
mise exec -- uv sync --project apps/mcp --locked
```

## 本地开发

```bash
mise exec -- pnpm dev:web
mise exec -- pnpm dev:api
mise exec -- pnpm dev:mcp
```

Vite 默认将 `/api` 代理到 `http://127.0.0.1:8321`，可通过 `VITE_API_PROXY_TARGET` 覆盖。
API 默认监听 `127.0.0.1:8321`。可在 `apps/api/.env` 中设置 `TICKLY_HOST` 和
`TICKLY_PORT` 覆盖；端口变化后，应同步调整 `VITE_API_PROXY_TARGET`，例如
`http://127.0.0.1:9000`。
API 当前提供以下业务路由以及 `/health`、`/ready` 和 FastAPI 文档路由：

- `/api/v1/auth/login`
- `/api/v1/auth/refresh`
- `/api/v1/auth/logout`
- `/api/v1/auth/me`
- `/api/v1/tasks`
- `/api/v1/tasks/topics`
- `/api/v1/tasks/parent-options`
- `/api/v1/tasks/{task_id}`

任务 API 支持 CRUD、三种状态流转、主题与状态筛选、排序、稳定 cursor 分页、主题列表和父待办候选。`/ready` 仅在应用 lifespan 已启动、数据库可访问且数据库 revision 与代码中的 migration head 一致时返回成功。

本地 API 开发可复制 `apps/api/.env.example`；其中 JWT 密钥仅用于开发。用户名会先去除首尾空白并转为小写，只允许 3–32 位小写字母、数字、下划线和连字符。
本地联调 MCP 时再复制 `apps/mcp/.env.example`，并把同一个
`TICKLY_MCP_TOKEN_SHA256` 摘要分别写入 `apps/api/.env` 与 `apps/mcp/.env`；
原始 Token 仍只放在调用客户端环境中。

## 账号管理

先完成 migration，然后从仓库根目录运行 CLI。密码最少 6 个字符，只通过交互式 `getpass` 读取，不接受命令行明文参数：

```bash
mise exec -- uv --directory apps/api run python -m app.cli user create --username potato
mise exec -- uv --directory apps/api run python -m app.cli user change-password --username potato
mise exec -- uv --directory apps/api run python -m app.cli user deactivate --username potato
mise exec -- uv --directory apps/api run python -m app.cli user revoke-sessions --username potato
```

第一版只允许一个账号，不提供公开注册、邮箱登录、账号重新激活或找回密码。access token 有效期默认 15 分钟且只保存在 Web 内存；refresh token 使用固定 30 天绝对期限，只写入 HttpOnly Cookie，数据库仅保存 SHA-256 摘要。

## 远程 MCP

`apps/mcp` 通过无状态 Streamable HTTP `/mcp` 暴露当前唯一启用账号的任务能力。它只调用 API 的内部契约，不直接访问 SQLite；MCP Bearer Token 也不能访问公开 Todo API。服务恰好提供以下七个工具：

- `list_tasks`：按状态、主题、排序和 cursor 分页列出任务组。
- `get_task`：按账号内 `serial` 读取任务及直接子任务。
- `list_topics`：列出当前账号已有的精确主题值。
- `find_parent_tasks`：查找可以作为父任务的根任务。
- `create_task`：创建根任务或一层子任务。
- `update_task`：更新普通可写字段，不修改状态。
- `set_task_status`：切换 New、In Progress 或 Completed 状态。

MCP 不提供删除、批量写入、任意 HTTP 转发或 SQL 工具。任务规则、账号所有权、父子约束、流水号和事务仍由 API 决定。

### Token 与 Codex 配置

在启动 Codex 的主机生成 32 个随机字节，并只输出其 SHA-256 摘要。以下 PowerShell 命令把原始 Token 保留在当前进程环境中：

```powershell
$env:TICKLY_MCP_TOKEN = [Convert]::ToHexString(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()
$tokenHash = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes($env:TICKLY_MCP_TOKEN)
  )
).ToLowerInvariant()
$tokenHash
```

POSIX shell 可使用：

```bash
export TICKLY_MCP_TOKEN="$(openssl rand -hex 32)"
printf %s "$TICKLY_MCP_TOKEN" | sha256sum | cut -d ' ' -f 1
```

原始 `TICKLY_MCP_TOKEN` 只能存在于 Codex 主机的安全环境中，不能写入服务器 `.env`、日志或仓库。把命令输出的 64 位小写摘要配置到服务器根 `.env` 的 `TICKLY_MCP_TOKEN_SHA256`，API 和 MCP 会由 Compose 接收同一个摘要。

在同一环境中用 Codex CLI 添加远程 Streamable HTTP 服务：

```shell
codex mcp add tickly --url https://tickly.example.com/mcp --bearer-token-env-var TICKLY_MCP_TOKEN
```

将示例域名替换为真实可信 HTTPS 入口。当前 Codex CLI 的 `--env` 只适用于 stdio server；远程 Bearer Token 应使用 `--bearer-token-env-var`，且启动 Codex 的进程必须能读取该环境变量。连接后先执行只读工具 smoke，再按 Codex 的写操作审批策略验证写工具。

完整的 Token 生成、Codex 配置、环境变量错误排查和最小验收步骤见：[MCP 客户端接入与部署说明](docs/mcp-client-deployment.md)。

## 检查

```bash
mise exec -- pnpm check
```

也可分别运行 `mise exec -- pnpm lint`、`mise exec -- pnpm typecheck`、`mise exec -- pnpm build`、`mise exec -- pnpm test:web`、`mise exec -- pnpm test:mcp` 和 `mise exec -- pnpm test:api`。

## Docker

常用命令：`mise exec -- pnpm docker:build`、`mise exec -- pnpm docker:up`、`mise exec -- pnpm docker:down`。
Compose 只发布 Web/Caddy 的 `8080` 端口，API 和 MCP 只暴露在 Compose 内网；API、MCP 和 Web 三个运行容器均使用非 root 用户。Caddy 代理 `/api/*` 与 `/mcp`，并在 SPA fallback 前将 `/internal/*` 固定为 `404`。

### GHCR 镜像发布

仓库 CI 在 pull request 中只执行全仓检查；`main` push 或 `v*` tag 必须先通过同一检查，之后才会构建并推送以下发布目标：

- `ghcr.io/fly-potato/tickly-api`
- `ghcr.io/fly-potato/tickly-mcp`
- `ghcr.io/fly-potato/tickly-web`

workflow 会为三个镜像构建 `linux/amd64` 与 `linux/arm64` manifest。`main` 生成 `latest` 与 `sha-*`，`v1.2.3` 同时生成 `v1.2.3`、`1.2.3` 与 `sha-*`。生产部署只能选择已经验证的版本或 `sha-*` 标签，不能使用会随主线漂移的 `latest`。

首次由 workflow 创建的 GHCR Package 默认按 Private 处理。确认三个包都关联 `Fly-Potato/tickly`、digest 和来源提交正确后，需要在每个 Package 的设置页分别改为 Public，并从未登录 GHCR 的环境执行匿名 pull。Public 可见性不可恢复为 Private，因此该操作不属于普通 CI 配置变更，也不能因为仓库本身公开就假定镜像已经公开。

矩阵任务不会跨三个 Package 原子提交。部署前必须确认 API、MCP、Web 的目标标签都存在、来源 revision 一致且整个 workflow 成功。

### Traefik 线上部署

根 `compose.yaml` 保留本地构建与容器 smoke；线上通过 `compose.traefik.yaml` 覆盖为 GHCR 镜像。只有 Web/Caddy 加入已有的 Traefik 外部网络，三个服务都不发布宿主机端口。Traefik 负责 TLS 和域名路由，Caddy 继续原样处理 `/api/*`、`/mcp`、`/internal/*` 与 SPA fallback，不要配置 `StripPrefix`。

根 `.env` 至少需要替换以下生产配置：

```dotenv
TICKLY_JWT_SECRET=replace-with-at-least-32-random-characters
TICKLY_MCP_TOKEN_SHA256=replace-with-the-generated-lowercase-sha256
TICKLY_MCP_ALLOWED_HOSTS=["tickly.example.com"]
TICKLY_MCP_ALLOWED_ORIGINS=["https://tickly.example.com"]
```

Traefik 线上部署还需要：

```dotenv
TICKLY_IMAGE_TAG=v1.0.0
TICKLY_DOMAIN=tickly.example.com
TICKLY_TRAEFIK_NETWORK=traefik
TICKLY_TRAEFIK_ENTRYPOINT=websecure
TICKLY_TRAEFIK_CERT_RESOLVER=letsencrypt
```

不要把原始 `TICKLY_MCP_TOKEN` 写入该文件。`TICKLY_MCP_ALLOWED_HOSTS` 应匹配入口实际传给 MCP 的 `Host`；`TICKLY_MCP_ALLOWED_ORIGINS` 只在请求带 `Origin` 时参与校验，无 `Origin` 的 Codex 请求仍可进入。生产环境不接受空白名单。根 `.env.example` 中的本机 HTTP 值仅用于本地验证。

首次部署或新数据库先执行 migration，再启动服务：

```bash
docker compose run --rm api python -m alembic upgrade head
mise exec -- pnpm docker:up
docker compose ps
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
docker compose exec mcp python -c "import os, urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{}/ready'.format(os.environ['TICKLY_MCP_PORT'])).read().decode())"
```

使用 Traefik 覆盖文件时，先确认外部网络已经由现有 Traefik 创建，再拉取同一标签的三套镜像、执行 migration 并启动：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml pull
docker compose -f compose.yaml -f compose.traefik.yaml run --rm api python -m alembic upgrade head
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
docker compose -f compose.yaml -f compose.traefik.yaml ps
curl --fail https://tickly.example.com/health
curl --fail https://tickly.example.com/ready
```

将示例域名替换为 `.env` 中的 `TICKLY_DOMAIN`；Compose 会读取 `.env`，但普通 shell 不会自动把其中变量导出给 `curl`。

升级前备份 SQLite volume，并记录当前 migration revision、镜像标签和 digest。把 `.env` 中的 `TICKLY_IMAGE_TAG` 改为新的已验证版本后，重复 `pull`、migration 与 `up --detach`。应用 smoke 失败时，将标签恢复到上一已知健康版本并重新拉取、启动；若新版本执行了不可逆 migration，应从部署前备份恢复数据库，不能用容器回滚代替数据恢复。

API `/health` 只检查进程，API `/ready` 还检查数据库与 migration。MCP `/health` 只检查进程；MCP `/ready` 要求合法 Token 摘要且 API `/ready` 成功，Compose 使用它作为 MCP 健康检查。公网 Caddy 不暴露 MCP 的探针路径。

Caddy 仍只监听容器内 HTTP `:8080`，证书与生产 TLS 由仓库外已有的 Traefik 实例提供。真实远程 Codex 连接必须通过该可信 HTTPS 入口；仅验证基础 Compose 的本机 HTTP 不能视为远程生产就绪。

轮换 Token 时重新生成原始值与摘要，更新服务器 `TICKLY_MCP_TOKEN_SHA256` 后重建 API/MCP，并让新 Codex 进程读取新的原始值：

```bash
docker compose up --detach --force-recreate api mcp
docker compose ps
```

首版不支持新旧 Token 并存；服务采用新摘要后旧 Token 立即失效，轮换期间应预留短暂连接中断。

故障排查应按响应位置和错误类型区分：

- MCP 容器无法启动或连接被拒绝：先查看 `docker compose logs mcp`。任意环境中的非法摘要，或生产环境缺少 `TICKLY_MCP_TOKEN_SHA256`，都会让 `Settings` 校验失败，服务不会开始监听端口。
- 已启动的 MCP 容器内 `/ready` 返回 `503`：检查非生产环境是否未配置 Token 摘要、MCP 生命周期中的 HTTP client 是否尚未启动，以及 API `/ready` 是否不可达或返回非 `200`。生产环境缺少或使用非法摘要时会在启动阶段失败，不会进入 `/ready`；该探针也不校验账号数量、账号状态或 Host/Origin 白名单。
- MCP `/ready` 正常，但工具返回 `mcp_account_unavailable`：检查数据库是否恰好存在一个账号且该账号已启用；零账号、多账号或唯一账号停用都会失败关闭。
- `/mcp` 返回 `421` 或 `403`：分别检查请求 `Host` 是否命中 `TICKLY_MCP_ALLOWED_HOSTS`、请求 `Origin` 是否命中 `TICKLY_MCP_ALLOWED_ORIGINS`。无 `Origin` 的 Codex 非浏览器请求不受 Origin 白名单拒绝。
