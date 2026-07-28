# Tickly

Tickly 是一个计划接入 AI 能力的个人多设备 Todo 应用 monorepo。

- `apps/web`：React + Vite 前端
- `apps/api`：由 uv 管理的 FastAPI 后端
- `packages/*`：可复用 workspace 包
- `docs/roadmaps`：0 到 1 产品与工程路线图
- `docs/superpowers`：已确认的设计与实施计划

当前已完成工程与容器基线、SQLAlchemy/SQLite 持久化，以及单账号用户名 + JWT 认证闭环。Web 已提供登录、认证恢复和退出；API 已提供登录、refresh 轮换、登出和当前用户接口；账号只能通过后端 CLI 创建和维护。Todo 业务与 AI 能力尚未实现。

数据库 schema 通过 Alembic 显式管理。API 本地默认使用 `apps/api/data/tickly.db`，首次运行前执行：

```bash
cd apps/api
mise exec -- uv run alembic upgrade head
mise exec -- uv run alembic current
```

回退本地 schema：

```bash
mise exec -- uv run alembic downgrade base
```

应用启动不会自动创建或修改生产数据库 schema。
首份 migration 已直接采用 `username`。如果本地曾用旧版本首份 migration 创建过无须保留的数据库，应停止 API、删除 `apps/api/data/tickly.db`，再重新执行 `alembic upgrade head`；本项目不提供旧开发数据兼容 migration。

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
API 当前提供 `/health`、`/ready`、`/api/v1/auth/login`、`/api/v1/auth/refresh`、`/api/v1/auth/logout`、`/api/v1/auth/me` 以及 FastAPI 文档路由；`/ready` 仅在应用 lifespan 已启动、数据库可访问且数据库 revision 与代码中的 migration head 一致时返回成功。

本地 API 开发可复制 `apps/api/.env.example`；其中 JWT 密钥仅用于开发。用户名会先去除首尾空白并转为小写，只允许 3–32 位小写字母、数字、下划线和连字符。

## 账号管理

先完成 migration，然后从 `apps/api` 目录运行 CLI。密码最少 6 个字符，只通过交互式 `getpass` 读取，不接受命令行明文参数：

```bash
mise exec -- uv run python -m app.cli user create --username potato
mise exec -- uv run python -m app.cli user change-password --username potato
mise exec -- uv run python -m app.cli user deactivate --username potato
mise exec -- uv run python -m app.cli user revoke-sessions --username potato
```

第一版只允许一个账号，不提供公开注册、邮箱登录、账号重新激活或找回密码。access token 有效期默认 15 分钟且只保存在 Web 内存；refresh token 使用固定 30 天绝对期限，只写入 HttpOnly Cookie，数据库仅保存 SHA-256 摘要。

## 检查

```bash
mise exec -- pnpm check
```

也可分别运行 `pnpm lint`、`pnpm typecheck`、`pnpm build`、`pnpm test:web` 和 `pnpm test:api`。

## Docker

启动可用的 Docker daemon 后运行完整容器 smoke test。该入口由 Node.js 执行，可在 Windows、macOS 和 Linux 使用，不依赖 Bash：

```bash
mise exec -- pnpm docker:smoke
```

常用命令：`pnpm docker:build`、`pnpm docker:up`、`pnpm docker:down`。
Compose 只发布 Web 的 `8080` 端口，API 通过内部网络由 Caddy 代理；两个运行容器均使用非 root 用户。
生产 Compose 强制要求 `TICKLY_JWT_SECRET`，且密钥至少 32 个字符。复制根 `.env.example` 为 `.env` 后必须替换示例值；不要复用开发默认密钥或提交 `.env`。`docker:smoke` 会为一次性 Compose project 在进程内生成随机密钥和密码，并验证真实账号创建与登录流程。
