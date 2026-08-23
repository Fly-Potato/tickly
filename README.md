# Tickly

Tickly 是一个面向个人多设备使用的 Todo 应用 monorepo，当前提供响应式 Web、FastAPI API 和远程 MCP 服务。

## 当前状态

已实现：

- 用户名登录、内存 access token、refresh session 和账号所有权隔离。
- Todo 的创建、编辑、删除确认、筛选、排序、cursor 分页和三种状态：`new`、`in_progress`、`completed`。
- 账号内 `serial`、主题、可选截止时间和一层父子待办。
- 无状态 Streamable HTTP MCP `/mcp`，提供七个受限 Todo 工具。
- Docker Compose 本地运行，以及 API、MCP、Web 三套多架构 GHCR 镜像发布配置。

尚未实现：模型调用、自然语言任务规划、公开注册、多人协作和离线同步。

## 项目结构

```text
tickly/
├── apps/web/       # React 19、Vite、Tailwind CSS 的 Todo Web
├── apps/api/       # FastAPI、SQLAlchemy、SQLite、Alembic 与认证
├── apps/mcp/       # 远程 MCP 服务，只通过 API 访问任务
├── packages/       # 跨应用包预留目录
├── docs/           # 开发、MCP、发布、部署和路线图文档
└── compose*.yaml   # 本地 Compose 与 Traefik 覆盖配置
```

整体请求链路为：

```text
Browser → Web/Caddy → FastAPI API → SQLite volume
                    └→ MCP client → MCP → FastAPI API
```

API 和 MCP 不直接向公网发布端口；生产环境由外部 Traefik 提供 HTTPS，Web/Caddy 负责同源路由。

## 快速开始

需要 Node.js 24、pnpm 11、Python 3.13、uv 和 mise。完整本地开发说明见 [docs/development.md](docs/development.md)。

```bash
mise install
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
mise exec -- uv sync --project apps/mcp --locked

mise exec -- uv --directory apps/api run alembic upgrade head
mise exec -- uv --directory apps/api run python -m app.cli user create --username potato
```

然后分别启动 Web、API 和 MCP：

```bash
mise exec -- pnpm dev:web
mise exec -- pnpm dev:api
mise exec -- pnpm dev:mcp
```

Web 默认运行在 Vite 地址，API 默认监听 `127.0.0.1:8321`；访问 Web 页面即可登录和使用 Todo。

## 文档导航

- [本地开发](docs/development.md)：依赖、migration、账号、环境变量、启动和测试。
- [MCP 客户端](docs/mcp.md)：工具清单、Token、Codex 配置和安全边界。
- [VPS 部署](docs/mcp-client-deployment.md)：GHCR 镜像、Traefik、备份、回滚和线上验收。
- [镜像发布](docs/release.md)：tag、GHCR、多架构构建和发布验收。
- [0→1 路线图](docs/roadmaps/2026-07-26-tickly-zero-to-one.md)：产品边界和阶段计划。
- [GHCR/Traefik 路线图](docs/roadmaps/2026-08-19-ghcr-traefik-deployment-roadmap.md)：发布与部署设计记录。

## 检查

```bash
mise exec -- pnpm check
```

也可以分别运行：

```bash
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:web
mise exec -- pnpm test:api
mise exec -- pnpm test:mcp
```

## Docker

本地构建和启动：

```bash
mise exec -- pnpm docker:build
mise exec -- pnpm docker:up
mise exec -- pnpm docker:down
```

基础 Compose 只发布 Web/Caddy 的 `8080` 端口，API 和 MCP 位于 Compose 内网。生产环境使用 `compose.traefik.yaml` 叠加 GHCR 镜像和外部 Traefik 网络；详细步骤见 [VPS 部署说明](docs/mcp-client-deployment.md)。

## 发布边界

只有推送 `v*` tag 时，发布 workflow 才会构建并推送：

- `ghcr.io/fly-potato/tickly-api`
- `ghcr.io/fly-potato/tickly-mcp`
- `ghcr.io/fly-potato/tickly-web`

GHCR Package 的可见性、匿名拉取和真实 HTTPS/MCP smoke 必须在远端分别验证，不能仅凭仓库配置判断已经完成。生产部署不要使用会漂移的 `latest` 标签。
