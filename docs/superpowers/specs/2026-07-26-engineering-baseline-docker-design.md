# Tickly 阶段 0：工程基线与 Docker 骨架设计

## 目标

在不实现数据库、认证、Todo 或 AI 功能的前提下，为 Tickly 建立可持续开发、测试和容器化的工程基线。

阶段完成后：

- Web lint、typecheck 与 build 全部通过。
- API 具备应用工厂、类型化配置、分层路由、统一错误、request ID、结构化日志和可测试的 lifespan。
- 本机开发继续使用 mise、pnpm 和 uv。
- Docker 只负责构建与验证生产形态镜像，不承担日常热更新开发。
- Caddy 能从单一入口提供 React SPA，并将 `/api/*` 原路径代理给 FastAPI。

## 设计边界

包含：

- 修复现有 Web fast-refresh lint 错误。
- FastAPI 工程结构调整。
- `pydantic-settings` 配置。
- `/health` 与 `/ready`。
- 统一错误响应。
- request ID 与结构化访问日志。
- Vite `/api` 开发代理。
- Web 与 API 生产镜像。
- 根 Compose 与自动化冒烟脚本。
- 根级统一检查与 Docker 命令。

不包含：

- SQLAlchemy、SQLite、Alembic 或持久卷。
- 登录、JWT、Cookie 或 CORS。
- Todo 页面或业务 API。
- Web 测试框架。
- HTTPS、真实域名或 VPS 发布。
- Docker 热更新或 bind-mounted source。
- CI/CD。

## 方案比较

### 方案 A：适度模块化（采用）

建立配置、路由、错误、日志和 middleware 边界，但不提前创建数据库、认证、service 或 repository 空壳。

优点：

- 足以支撑后续数据层和认证阶段。
- 每个文件职责单一。
- 当前不会产生无行为的架构文件。

代价：

- 阶段 1 和阶段 2 仍需按真实需求增加模块。

### 方案 B：完整分层骨架

提前创建未来全部目录与接口。

优点是后续文件位置已确定；缺点是现在会产生大量没有消费者的抽象，容易与实际需求偏离。因此不采用。

### 方案 C：继续扩展单文件

保留所有 API 行为在 `app/main.py`，只增加容器文件。

改动较少，但配置、错误、middleware 和后续业务路由会快速纠缠，阶段 1 必须再次重构。因此不采用。

## 目标目录结构

```text
tickly/
├── .dockerignore
├── compose.yaml
├── package.json
├── scripts/
│   └── docker-smoke.sh
└── apps/
    ├── api/
    │   ├── .env.example
    │   ├── Dockerfile
    │   ├── pyproject.toml
    │   ├── uv.lock
    │   ├── app/
    │   │   ├── api/
    │   │   │   ├── __init__.py
    │   │   │   ├── router.py
    │   │   │   └── routes/
    │   │   │       ├── __init__.py
    │   │   │       └── health.py
    │   │   ├── core/
    │   │   │   ├── __init__.py
    │   │   │   ├── config.py
    │   │   │   ├── errors.py
    │   │   │   └── logging.py
    │   │   ├── middleware/
    │   │   │   ├── __init__.py
    │   │   │   └── request_id.py
    │   │   ├── __init__.py
    │   │   └── main.py
    │   └── tests/
    │       ├── test_config.py
    │       ├── test_errors.py
    │       ├── test_health.py
    │       └── test_request_id.py
    └── web/
        ├── Caddyfile
        ├── Dockerfile
        ├── vite.config.ts
        └── src/
            └── components/
                └── ui/
                    ├── button-variants.ts
                    └── button.tsx
```

## API 结构

### Composition root

`apps/api/app/main.py` 提供：

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    ...


app = create_app()
```

`create_app()` 负责：

1. 解析或接收 Settings。
2. 配置日志。
3. 创建 lifespan。
4. 创建 FastAPI 实例。
5. 注册 request ID middleware。
6. 注册异常处理器。
7. 注册健康路由和 `/api/v1` 聚合路由。

测试使用显式 `Settings` 调用 `create_app()`，不修改进程全局环境，也不依赖可变的全局 Settings 缓存。

### Routing

基础设施端点不加 API 版本前缀：

- `GET /health`
- `GET /ready`

未来业务端点通过 `apps/api/app/api/router.py` 聚合，并统一挂载到 `/api/v1`。阶段 0 不创建虚假的业务端点。

### Lifespan

使用 FastAPI 当前的 `lifespan` async context manager，不使用已弃用的 `@app.on_event("startup")` 或 `@app.on_event("shutdown")`。

阶段 0 lifespan 负责：

- 验证配置已成功构造。
- 标记应用 ready。
- 记录启动与关闭日志。
- 关闭时清理 ready 状态。

涉及 lifespan 的测试必须使用：

```python
with TestClient(app) as client:
    ...
```

## 类型化配置

新增运行时依赖 `pydantic-settings`。

`Settings` 字段：

| 字段 | 类型 | 默认值 |
| --- | --- | --- |
| `environment` | `development | test | production` | `development` |
| `app_name` | `str` | `Tickly API` |
| `api_v1_prefix` | `str` | `/api/v1` |
| `log_level` | `DEBUG | INFO | WARNING | ERROR | CRITICAL` | `INFO` |
| `log_json` | `bool` | `false` |
| `request_id_header` | `str` | `X-Request-ID` |

配置规则：

- 环境变量统一使用 `TICKLY_` 前缀。
- 本机开发允许读取 `apps/api/.env`。
- 环境变量覆盖 dotenv。
- Docker 镜像不复制 `.env`。
- `apps/api/.env.example` 只包含变量名和非敏感示例。
- production 配置无效时应用必须启动失败，不能静默回退。
- 测试直接构造 Settings，不依赖开发者本机 `.env`。
- production Compose 显式设置 `TICKLY_LOG_JSON=true`。

阶段 0 不包含 JWT secret、数据库 URL 或 AI key。

## 健康与就绪

### `GET /health`

用途：证明 API 进程可接收请求。

响应：

```json
{
  "status": "ok"
}
```

该端点始终保持轻量，不访问数据库或外部服务。

### `GET /ready`

用途：证明当前实例已完成 lifespan 初始化。

ready 时：

```json
{
  "status": "ready"
}
```

未 ready 时返回：

```json
{
  "error": {
    "code": "not_ready",
    "message": "服务尚未就绪",
    "request_id": "4c9f...",
    "details": []
  }
}
```

状态码为 `503`。

阶段 1 接入数据库后，`/ready` 再增加数据库连接与 Alembic revision 检查。`/health` 不随之改变。

## request ID 与访问日志

使用自定义 ASGI middleware：

1. 从配置的 header 读取可选 request ID。
2. 只接受 1–128 位字母、数字、点、下划线或连字符。
3. 缺失或非法时生成 UUID。
4. 写入 request scope state。
5. 记录开始时间。
6. 调用下游应用。
7. 在响应中写入相同的 request ID header。
8. 输出访问日志。

结构化访问日志至少包含：

- UTC timestamp
- level
- message
- request_id
- method
- path
- status
- duration_ms

日志只输出到 stdout。`log_json=false` 时使用便于本机阅读的文本格式；`log_json=true` 时每行输出一个 JSON 对象。

阶段 0 不引入第三方日志框架。

## 统一错误契约

格式：

```json
{
  "error": {
    "code": "validation_error",
    "message": "请求参数无效",
    "request_id": "4c9f...",
    "details": []
  }
}
```

阶段 0 处理：

| 场景 | 状态码 | code |
| --- | --- | --- |
| 请求校验失败 | `422` | `validation_error` |
| 未知路由 | `404` | `not_found` |
| 显式 HTTP 错误 | 原状态码 | 稳定映射或 `http_error` |
| 未处理异常 | `500` | `internal_error` |
| readiness 未完成 | `503` | `not_ready` |

规则：

- 所有错误响应包含当前 request ID。
- `details` 没有额外信息时返回空数组。
- 请求校验错误的 `details` 只返回可公开的字段位置、错误类型和消息。
- 生产响应不包含 Python 堆栈、异常 repr 或内部文件路径。
- 未处理异常使用 `logger.exception` 记录堆栈，并向客户端返回稳定通用消息。
- 测试未处理异常时使用 `raise_server_exceptions=False`。

## Web 基线

### lint 修复

保留 `react-refresh/only-export-components` 规则。

- 将 `buttonVariants` 移到 `button-variants.ts`。
- `button.tsx` 只导出 `Button` React 组件。
- 所有现有引用同步到新位置。

不通过关闭规则、添加文件级 ignore 或降低 lint 严格度解决。

### Vite 开发代理

`vite.config.ts` 为开发服务器增加 `/api` proxy：

- 默认 target：`http://127.0.0.1:8000`
- 可通过 `VITE_API_PROXY_TARGET` 开发环境变量覆盖。
- Vite config 使用当前 mode 加载该变量，不把它写入浏览器业务代码。
- 保留 `/api` 原路径，不执行 rewrite。
- 不启用宽泛 CORS。
- 只在 `vite dev` 生效。

Web 业务代码始终使用相对路径 `/api/v1/...`。生产构建不包含 API host。

阶段 0 不修改当前页面，不引入路由、状态管理或 Web 测试依赖。

## Web Dockerfile

构建上下文为仓库根目录。

### Builder

- 使用 Node.js 24 slim 镜像。
- 使用根 `packageManager` 锁定的 pnpm 11.16.0。
- 先复制 workspace manifest、根 lockfile 和 Web package manifest。
- 使用 frozen lockfile 安装依赖。
- 再复制 Web 源码并构建 `@tickly/web`。

### Runtime

- 实施时解析当前稳定的 Caddy 2 Alpine patch tag 并固定写入，不使用 `latest` 或浮动 major tag。
- 只复制 `apps/web/dist` 和 `apps/web/Caddyfile`。
- 使用 `caddy` 用户。
- 监听容器端口 `8080`。

Caddyfile：

```text
:8080
  /api/*、/health、/ready → reverse_proxy api:8000
  其他请求 → /srv
  未命中文件 → /index.html
```

API handle 必须在 SPA fallback 之前，且保留 `/api` 前缀。

阶段 0 不配置域名、自动 HTTPS 或生产安全响应头。

## API Dockerfile

构建上下文为仓库根目录。

- 使用 Python 3.13 slim 镜像。
- uv 固定为根 `mise.toml` 中的 `0.11.32`。
- 从官方 uv 镜像复制 uv 二进制。
- 先复制 `apps/api/pyproject.toml` 和 `apps/api/uv.lock`。
- 执行 `uv sync --frozen --no-dev`。
- 再复制 `apps/api/app`。
- 创建不可登录的非 root 用户。
- 最终工作目录只包含运行应用需要的文件。
- 启动 FastAPI production runner，监听 `0.0.0.0:8000`。

运行镜像不包含：

- pytest 与开发依赖。
- `tests/`。
- uv cache。
- `.venv` 之外的构建工具。
- 本机 `.env`。

## Docker Compose

根目录新增 `compose.yaml`。

### `api`

- 从 `apps/api/Dockerfile` 构建。
- 只向 Compose 网络 `expose: 8000`。
- 不使用宿主机 `ports` 映射。
- 环境为 production。
- 显式设置 `TICKLY_LOG_JSON=true`。
- 单进程。
- healthcheck 直接请求容器内 `http://127.0.0.1:8000/health`。

### `web`

- 从 `apps/web/Dockerfile` 构建。
- 宿主机映射 `8080:8080`。
- 通过 `depends_on` 等待 `api` healthy。
- healthcheck 请求自身静态首页。

阶段 0 不定义数据库 volume、`migrate` 服务或生产 secret。

## Docker ignore

根 `.dockerignore` 至少排除：

- `.git`
- `.github`
- `.agents`
- `.codex`
- `.worktrees`
- `node_modules`
- `.pnpm-store`
- `.venv`
- `dist`
- `__pycache__`
- `.pytest_cache`
- uv 与工具缓存
- 日志
- `.env` 与 `.env.*`
- 编辑器文件
- `docs`

`.env.example` 不需要进入 build context，只用于开发者文档。

## 自动化命令

根 `package.json` 增加：

- `check`
- `docker:build`
- `docker:up`
- `docker:down`
- `docker:smoke`

`check` 严格依次运行：

1. Web lint。
2. Web typecheck。
3. Web build。
4. API pytest。

任一步失败立即返回非零状态。

`scripts/docker-smoke.sh`：

1. 验证 `docker compose config`。
2. 构建镜像。
3. 启动服务。
4. 等待 API 与 Web healthy，设置明确超时。
5. 请求 `http://127.0.0.1:8080/`，确认返回 Web HTML。
6. 请求 `http://127.0.0.1:8080/health`，确认返回 `{"status":"ok"}`。
7. 请求 `http://127.0.0.1:8080/ready`，确认返回 `{"status":"ready"}`。
8. 确认 API 的 8000 端口未映射到宿主机。
9. 确认 Web 与 API 主进程不是 root。
10. 无论成功或失败都执行 `docker compose down`，清理容器和网络，不删除镜像。

脚本只依赖 macOS 和常见 Linux 环境已有或明确要求安装的 shell、Docker 和 curl，不引入 jq。

## 测试

### `test_config.py`

- 默认开发配置。
- `TICKLY_` 前缀字段解析。
- 显式 Settings 不依赖本机 dotenv。
- 非法 environment 和 log level 失败。

### `test_health.py`

- lifespan 外部尚未 ready。
- `with TestClient` 内 `/health` 返回 200。
- `with TestClient` 内 `/ready` 返回 200。
- lifespan 结束后 ready 状态清理。

### `test_request_id.py`

- 缺失 header 时生成 request ID。
- 合法传入值被保留。
- 非法或过长值被替换。
- 响应 header 与错误体中的 request ID 相同。

### `test_errors.py`

- 404 使用统一结构。
- 请求校验错误使用 422 统一结构。
- 显式 HTTP 错误保留状态码。
- 未处理异常返回 500 通用结构，不泄露异常文本。

### Docker 冒烟

- Compose 配置合法。
- 镜像构建成功。
- 两个服务 healthy。
- Web 与 API 通过单一入口可访问。
- API 未暴露宿主机端口。
- 容器主进程非 root。
- 脚本退出后没有运行中的 Tickly 容器。

## 验收命令

从仓库根目录执行：

```bash
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
mise exec -- pnpm check
mise exec -- pnpm docker:smoke
git diff --check
```

验收结果：

- Web lint、typecheck、build 均为 0 个错误。
- API pytest 全部通过。
- Docker 冒烟全部通过并完成清理。
- 工作树不包含镜像构建产物、依赖目录、缓存或 `.env`。

## 文档更新

根 README 增加：

- 本机 Web/API 开发命令。
- `pnpm check`。
- Docker build、up、down 与 smoke 命令。
- 阶段 0 的 HTTP 访问地址 `http://localhost:8080`。
- Docker 仅用于生产形态验证，日常开发不在容器内运行。

`AGENTS.md` 的验证要求补充：

- Dockerfile、Caddyfile 或 Compose 改动必须运行 `docker:smoke`。
- API 结构或配置改动必须运行 API pytest。

## 实施顺序

1. 先为 lint 错误、配置、应用工厂、健康端点、错误与 request ID 写失败测试或复现检查。
2. 完成最小实现并使本机检查全绿。
3. 再创建 Web 与 API 镜像。
4. 最后创建 Compose 和 smoke 脚本。
5. Docker 验证通过后更新 README 与 AGENTS。

Docker 工作不得掩盖本机检查失败；只有本机检查全绿后才进入容器验证。

## 文档依据

- [FastAPI Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [FastAPI Lifespan Testing](https://fastapi.tiangolo.com/advanced/testing-events/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Vite 8 server.proxy](https://vite.dev/config/server-options.html#server-proxy)
- [uv FastAPI Docker](https://docs.astral.sh/uv/guides/integration/fastapi/)
- [uv Docker Integration](https://docs.astral.sh/uv/guides/integration/docker/)
- [Caddy SPA and API Routing](https://caddyserver.com/docs/caddyfile/patterns)
- [Docker Compose](https://docs.docker.com/compose/)
