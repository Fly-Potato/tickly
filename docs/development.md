# Tickly 本地开发

本文说明如何在本地安装依赖、初始化数据库、创建账号并启动 Tickly。项目根目录由 mise 管理 Node.js、pnpm 和 uv 版本。

## 工具与依赖

```bash
mise install
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
mise exec -- uv sync --project apps/mcp --locked
```

JavaScript 依赖只使用根目录 `pnpm-lock.yaml`；Python 依赖分别由 `apps/api/uv.lock` 和 `apps/mcp/uv.lock` 管理。

## 初始化 API 数据库

API 默认使用 `apps/api/data/tickly.db`。首次运行或数据库为空时执行：

```bash
mise exec -- uv --directory apps/api run alembic upgrade head
mise exec -- uv --directory apps/api run alembic current
```

应用启动不会自动创建或修改 schema。需要回退本地 schema 时：

```bash
mise exec -- uv --directory apps/api run alembic downgrade base
```

如果本地数据库来自不兼容的旧开发 migration，应停止 API、删除本地数据库后重新执行 `upgrade head`；项目不提供旧开发数据兼容 migration。

## 创建账号

账号仍只通过后端 CLI 创建和维护，不改变现有注册方式。可以为不同用户名重复执行 `user create` 来创建多个账号；重复用户名会被拒绝。密码至少 6 个字符，只通过交互式 `getpass` 输入：

```bash
mise exec -- uv --directory apps/api run python -m app.cli user create --username potato
mise exec -- uv --directory apps/api run python -m app.cli user change-password --username potato
mise exec -- uv --directory apps/api run python -m app.cli user deactivate --username potato
mise exec -- uv --directory apps/api run python -m app.cli user revoke-sessions --username potato
```

不提供公开注册、邮箱登录、账号重新激活或找回密码。登录用户可以在 Web `/settings` 修改自己的密码；管理员仍可用上述 CLI 修改密码、停用账号或撤销 Web 会话。

## 环境变量

本地 API 可复制 `apps/api/.env.example` 为 `apps/api/.env`；本地 MCP 联调可复制 `apps/mcp/.env.example`。开发环境的 JWT 密钥只用于本地，不能提交。

API 默认监听 `127.0.0.1:8321`。可在 `apps/api/.env` 设置 `TICKLY_HOST` 和 `TICKLY_PORT`；端口变化后同步设置 Web 的 `VITE_API_PROXY_TARGET`。

MCP 服务只需要 API 地址和传输层 Host/Origin 白名单，不配置共享 Token。用户级原始 Token 只放在调用客户端的 `TICKLY_MCP_TOKEN` 环境变量中，不写入 API/MCP `.env`、日志或仓库。

## 启动服务

分别打开终端运行：

```bash
mise exec -- pnpm dev:web
mise exec -- pnpm dev:api
mise exec -- pnpm dev:mcp
```

Web 使用 Vite 开发服务器，`/api` 默认代理到 `http://127.0.0.1:8321`。API 提供 `/health`、`/ready` 和 FastAPI 文档路由；`/ready` 还会检查数据库可访问且 migration revision 与代码中的 head 一致。

服务启动后，按 Vite 终端输出打开 Web（默认端口为 `5173`），使用 CLI 创建的账号登录并进入 `/settings`。在设置页创建具名 MCP Token 后立即保存只显示一次的原始值，再按 [MCP 客户端说明](mcp.md) 配置 `http://127.0.0.1:8322/mcp`。一个用户可以为不同客户端分别创建和撤销多个 Token。

## 检查与测试

```bash
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:web
mise exec -- pnpm test:api
mise exec -- pnpm test:mcp
mise exec -- pnpm check
```

修改范围较小时，优先运行对应应用的测试；涉及 API/MCP 内部契约时同时运行 API 与 MCP 测试。
