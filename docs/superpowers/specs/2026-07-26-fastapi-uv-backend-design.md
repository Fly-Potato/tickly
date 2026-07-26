# Tickly FastAPI + uv 最小后端设计

## 目标

在现有 pnpm monorepo 中新增一个最小、可运行、可测试的 FastAPI 后端。后端位于 `apps/api`，使用 uv 管理 Python 解释器、虚拟环境、项目依赖和锁文件，同时保持现有 Web 应用行为不变。

## 技术边界

- 根 `mise.toml` 继续管理 Node.js 24 和 pnpm 11，并新增 uv。uv 版本在实施时解析当前稳定版本后精确写入，避免使用浮动的 `latest`。
- uv 独立管理后端 Python 3.13、`apps/api/.venv`、`pyproject.toml` 和 `uv.lock`。
- mise 不再声明 Python，避免 mise 与 uv 同时管理同一个解释器。
- `apps/api` 是独立 uv 项目。本次不创建根级 Python `pyproject.toml` 或 uv workspace。
- 如果未来出现第二个 Python 服务或共享 Python 包，再将这些项目提升为根 uv workspace。

## 目录结构

```text
tickly/
├── apps/
│   ├── web/
│   └── api/
│       ├── app/
│       │   ├── __init__.py
│       │   └── main.py
│       ├── tests/
│       │   └── test_health.py
│       ├── .python-version
│       ├── pyproject.toml
│       └── uv.lock
├── package.json
├── mise.toml
└── README.md
```

`apps/api` 是不可发布的应用项目，不构建 Python wheel，也不增加 `src/` 包装层。当前仅有两个 Python 模块时，直接使用 `app/` 可以减少不必要的打包配置。

## Python 与依赖管理

uv 负责以下内容：

- `.python-version` 固定 Python 3.13。
- `.venv` 保存项目虚拟环境，并由 Git 忽略。
- `pyproject.toml` 声明项目元数据与依赖。
- `uv.lock` 保存跨平台解析结果并提交 Git。
- `uv sync` 创建或同步环境。
- `uv run` 在同步后的环境中执行 FastAPI 和 pytest，不要求开发者手动激活虚拟环境。

应用依赖仅包含：

```toml
dependencies = [
  "fastapi[standard]",
]
```

开发依赖仅包含 pytest。暂不加入数据库驱动、ORM、迁移工具、配置库、日志库、类型检查器、代码格式化器或 linter。

## API 行为

应用创建一个 `FastAPI` 实例，并提供一个端点：

```http
GET /health
```

成功响应：

```json
{
  "status": "ok"
}
```

响应状态为 `200 OK`。该端点不访问数据库或外部服务，只证明进程、路由和 ASGI 应用可用。

FastAPI 自带的 OpenAPI 与交互式文档保持默认启用：

- `/docs`
- `/redoc`
- `/openapi.json`

## 开发入口

后端开发服务器的标准命令为：

```bash
cd apps/api
uv run fastapi dev app/main.py
```

根 `package.json` 保留现有 `dev` 作为 Web 应用入口，并新增两个协调脚本：

```json
{
  "scripts": {
    "dev:api": "cd apps/api && uv run fastapi dev app/main.py",
    "test:api": "cd apps/api && uv run pytest"
  }
}
```

pnpm 只负责从仓库根目录转发命令，不参与 Python 依赖解析。

## 测试

`tests/test_health.py` 使用 FastAPI 提供的 `TestClient`：

1. 请求 `GET /health`。
2. 断言响应状态为 200。
3. 断言 JSON 内容严格等于 `{"status": "ok"}`。

测试命令：

```bash
cd apps/api
uv run pytest
```

根目录也可运行：

```bash
mise exec -- pnpm test:api
```

## Git 与忽略规则

提交：

- `apps/api/.python-version`
- `apps/api/pyproject.toml`
- `apps/api/uv.lock`
- 应用源码与测试

忽略：

- `.venv/`
- `__pycache__/`
- `.pytest_cache/`
- `*.py[cod]`

现有 pnpm lockfile 与新的 uv lockfile 分别管理 JavaScript 和 Python 依赖，互不替代。

## README

根 README 增加：

- `mise install` 安装 Node.js、pnpm 和 uv。
- `uv sync --project apps/api` 同步后端环境。
- `mise exec -- pnpm dev:api` 启动后端。
- `mise exec -- pnpm test:api` 运行后端测试。
- Web 与 API 的目录说明。

## 错误处理

- uv 无法找到 Python 3.13 时，由 uv 自动安装受管理的解释器后重试同步。
- 依赖同步失败时保留 uv 的原始错误，不回退到 pip。
- 健康检查不捕获异常；由于没有外部依赖，初始化失败应直接阻止服务启动。
- 本次不增加全局异常处理中间件或自定义错误响应模型。

## 验收标准

1. mise 能解析并运行固定版本的 uv。
2. 在 `apps/api` 中运行 `uv sync --locked` 成功。
3. 在 `apps/api` 中运行 `uv run pytest` 成功，且健康检查测试为 1 个通过、0 个失败。
4. 在 `apps/api` 中运行 `uv run fastapi dev app/main.py` 能启动应用。
5. `GET /health` 返回 `200` 和 `{"status": "ok"}`。
6. 根 `mise exec -- pnpm test:api` 成功。
7. 现有 Web 构建与类型检查继续成功。
8. `uv.lock` 已提交，`.venv` 与 Python 缓存未进入 Git。

## 非目标

本次不加入数据库、ORM、迁移、认证、授权、CORS、环境变量配置模型、Docker、部署配置、后台任务、消息队列、缓存、结构化日志、API 版本前缀或前后端联调。
