# Tickly

Tickly 是一个计划接入 AI 能力的个人多设备 Todo 应用 monorepo。

- `apps/web`：React + Vite 前端
- `apps/api`：由 uv 管理的 FastAPI 后端
- `packages/*`：可复用 workspace 包
- `docs/roadmaps`：0 到 1 产品与工程路线图
- `docs/superpowers`：已确认的设计与实施计划

Todo 领域、登录、数据库和 AI 行为尚未实现；当前阶段完成工程基线与容器部署骨架。

## 初始化

```bash
mise install
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
```

## 本地开发

```bash
mise exec -- pnpm dev
mise exec -- pnpm dev:api
```

Vite 默认将 `/api` 代理到 `http://127.0.0.1:8000`，可通过 `VITE_API_PROXY_TARGET` 覆盖。
API 当前提供 `/health`、`/ready` 以及 FastAPI 文档路由；`/ready` 仅在应用 lifespan 已启动时返回成功。

## 检查

```bash
mise exec -- pnpm check
```

也可分别运行 `pnpm lint`、`pnpm typecheck`、`pnpm build` 和 `pnpm test:api`。

## Docker

启动 Docker Desktop 后运行完整容器 smoke test：

```bash
mise exec -- pnpm docker:smoke
```

常用命令：`pnpm docker:build`、`pnpm docker:up`、`pnpm docker:down`。
Compose 只发布 Web 的 `8080` 端口，API 通过内部网络由 Caddy 代理；两个运行容器均使用非 root 用户。
