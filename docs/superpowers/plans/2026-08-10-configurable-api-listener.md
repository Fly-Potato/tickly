# 可配置 API 监听参数实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Tickly API 在本地开发和 Docker Compose 中都能通过 `TICKLY_HOST`、`TICKLY_PORT` 配置监听 IP 与端口。

**Architecture:** 在现有 Pydantic `Settings` 中校验监听配置，新增一个只负责读取设置并调用 Uvicorn 的 `app.server` 模块。本地开发和 Docker 共用该入口；Compose 将同一个端口值传给 API、健康检查和 Caddy，smoke test 使用非默认端口验证完整链路。

**Tech Stack:** Python 3.13、Pydantic Settings 2.x、FastAPI 0.140+、Uvicorn、pytest、Docker Compose、Caddy、Node.js

## Global Constraints

- 环境变量固定命名为 `TICKLY_HOST`、`TICKLY_PORT`。
- 本地默认监听 `127.0.0.1:8000`；Docker Compose 默认监听容器内 `0.0.0.0:8000`。
- `TICKLY_HOST` 只接受合法 IPv4 或 IPv6 地址；`TICKLY_PORT` 只接受 `1` 到 `65535` 的整数。
- Web 对外发布端口保持 `8080`，不新增 workers、TLS、Unix socket 或服务器抽象层。
- 本地启动启用 reload，Docker 生产启动不启用 reload。
- 测试注释、测试说明和断言失败说明使用中文；关键配置与容器边界使用中文注释。
- 不新增依赖；Uvicorn 已由 `fastapi[standard]` 提供。

---

## 文件结构

- `apps/api/app/core/config.py`：声明并校验 API 监听设置。
- `apps/api/app/server.py`：统一解析启动模式并调用 Uvicorn，不承载 FastAPI 业务逻辑。
- `apps/api/tests/test_config.py`：覆盖监听设置的默认值、环境覆盖与非法值。
- `apps/api/tests/test_server.py`：覆盖监听参数向 Uvicorn 的传递和 reload 开关。
- `package.json`：让本地开发命令调用统一启动入口。
- `apps/api/Dockerfile`：让生产容器调用统一启动入口，移除固定监听参数。
- `compose.yaml`：把同一监听配置传给 API、健康检查与 Web/Caddy。
- `apps/web/Caddyfile`：按环境变量选择 API 上游端口。
- `scripts/docker-smoke.mjs`：使用非默认 API 端口验证容器真实闭环。
- `apps/api/.env.example`、`.env.example`、`README.md`：说明本地与 Compose 配置边界。

### Task 1: 在 Settings 中校验监听 IP 与端口

**Files:**
- Modify: `apps/api/app/core/config.py:1-49`
- Test: `apps/api/tests/test_config.py:1-89`

**Interfaces:**
- Consumes: 现有 `Settings` 的 `TICKLY_` 前缀和 `.env` 加载规则。
- Produces: `Settings.host: IPvAnyAddress`、`Settings.port: int`，供 `app.server` 读取。

- [x] **Step 1: 写入默认值与环境覆盖的失败测试**

在 `test_default_settings_are_for_local_development` 中加入：

```python
assert str(settings.host) == "127.0.0.1"
assert settings.port == 8000
```

再新增环境覆盖与非法值测试：

```python
def test_listener_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TICKLY_HOST", "::")
    monkeypatch.setenv("TICKLY_PORT", "9000")

    settings = Settings(_env_file=None)

    assert str(settings.host) == "::"
    assert settings.port == 9000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "localhost"),
        ("host", "127.0.0.1/24"),
        ("port", 0),
        ("port", 65536),
    ],
)
def test_invalid_listener_settings_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value}, _env_file=None)
```

- [x] **Step 2: 运行测试并确认因字段不存在而失败**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm test:api -- tests/test_config.py -q
```

Expected: FAIL；默认值测试报告 `Settings` 没有 `host` 或 `port` 属性，非法值测试因未知字段被忽略而没有抛出 `ValidationError`。

- [x] **Step 3: 实现最小监听字段和范围类型**

在 `apps/api/app/core/config.py` 调整导入并声明类型：

```python
from ipaddress import IPv4Address
from typing import Annotated, Literal

from pydantic import Field, IPvAnyAddress, PositiveInt, field_validator, model_validator

Port = Annotated[int, Field(ge=1, le=65535)]
```

在 `Settings` 的日志字段之后加入：

```python
host: IPvAnyAddress = IPv4Address("127.0.0.1")
port: Port = 8000
```

- [x] **Step 4: 运行目标测试并确认默认值、覆盖和非法值全部通过**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm test:api -- tests/test_config.py -q
```

Expected: PASS。`IPvAnyAddress` 拒绝主机名和 CIDR，`Port` 拒绝范围外整数。

- [x] **Step 5: 提交 Settings 改动**

```bash
git add apps/api/app/core/config.py apps/api/tests/test_config.py
git commit -m "feat(api): 增加监听参数配置"
```

### Task 2: 新增统一的 Uvicorn 启动入口

**Files:**
- Create: `apps/api/app/server.py`
- Create: `apps/api/tests/test_server.py`
- Modify: `package.json:12`

**Interfaces:**
- Consumes: `Settings.host`、`Settings.port`。
- Produces: `main(argv: Sequence[str] | None = None) -> None`；支持可选 `--reload`，并调用 `uvicorn.run("app.main:app", host=..., port=..., reload=...)`。

- [x] **Step 1: 写入服务器参数传递的失败测试**

创建 `apps/api/tests/test_server.py`：

```python
from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("arguments", "expected_reload"),
    [([], False), (["--reload"], True)],
    ids=["生产模式", "开发热重载"],
)
def test_server_passes_listener_settings_to_uvicorn(
    arguments: list[str],
    expected_reload: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = import_module("app.server")
    captured: dict[str, object] = {}

    def fake_run(application: str, **options: object) -> None:
        captured["application"] = application
        captured.update(options)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    monkeypatch.setenv("TICKLY_HOST", "0.0.0.0")
    monkeypatch.setenv("TICKLY_PORT", "9100")

    server.main(arguments)

    assert captured == {
        "application": "app.main:app",
        "host": "0.0.0.0",
        "port": 9100,
        "reload": expected_reload,
    }
```

- [x] **Step 2: 运行测试并确认模块缺失导致失败**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm test:api -- tests/test_server.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.server'`，异常发生在测试函数内部。

- [x] **Step 3: 实现最小服务器入口**

创建 `apps/api/app/server.py`：

```python
import argparse
from collections.abc import Sequence

import uvicorn

from app.core.config import Settings


def run_server(*, reload: bool = False) -> None:
    """读取统一配置并启动 API 服务器。

    监听配置在打开网络端口前完成校验；reload 只由本地开发命令开启，
    生产容器必须保持单进程、无文件监视的启动方式。
    """
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=str(settings.host),
        port=settings.port,
        reload=reload,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动 Tickly API")
    parser.add_argument("--reload", action="store_true", help="启用开发热重载")
    arguments = parser.parse_args(argv)
    run_server(reload=arguments.reload)


if __name__ == "__main__":
    main()
```

- [x] **Step 4: 运行服务器入口测试并确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm test:api -- tests/test_server.py -q
```

Expected: `2 passed`。

- [x] **Step 5: 切换本地开发脚本并验证参数解析**

把根 `package.json` 中的脚本改为：

```json
"dev:api": "cd apps/api && uv run python -m app.server --reload"
```

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm dev:api -- --help
```

Expected: 退出码为 `0`，帮助信息包含 `--reload`，且不会启动网络服务。

- [x] **Step 6: 提交统一启动入口**

```bash
git add apps/api/app/server.py apps/api/tests/test_server.py package.json
git commit -m "feat(api): 统一开发与生产启动入口"
```

### Task 3: 让 Docker Compose 的 API 端口形成单一配置链路

**Files:**
- Modify: `apps/api/Dockerfile:32-38`
- Modify: `compose.yaml:2-36`
- Modify: `apps/web/Caddyfile:5-7`
- Modify: `scripts/docker-smoke.mjs:5-10,126-173`

**Interfaces:**
- Consumes: `python -m app.server`、Compose 插值 `${TICKLY_HOST:-0.0.0.0}` 和 `${TICKLY_PORT:-8000}`。
- Produces: API、健康检查和 Caddy 共用的容器内端口；smoke test 固定注入非默认端口 `18080`。

- [x] **Step 1: 先让 smoke test 要求非默认 API 端口**

在 `scripts/docker-smoke.mjs` 常量区加入并注入：

```javascript
const apiPort = "18080"

// 使用非默认端口验证 API、健康检查和 Caddy 上游共享同一份配置。
process.env.TICKLY_PORT = apiPort
```

新增检查函数：

```javascript
async function assertContainerEnvironment(container, name, expectedValue) {
  const result = await run(
    "docker",
    ["inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container],
    { capture: true },
  )
  const entries = result.stdout.split("\n")
  if (!entries.includes(`${name}=${expectedValue}`)) {
    throw new Error(`API 容器未使用 ${name}=${expectedValue}`)
  }
}
```

在取得 `apiContainer` 后加入：

```javascript
await assertContainerEnvironment(apiContainer, "TICKLY_PORT", apiPort)
```

- [ ] **Step 2: 运行 smoke test 并确认当前 Compose 不满足要求**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm docker:smoke
```

Expected: FAIL，API 容器没有 `TICKLY_PORT=18080`。若 Docker daemon 不可用，保留失败证据，并在 daemon 可用后补跑这一红灯步骤。

- [x] **Step 3: 切换 Dockerfile 到统一入口**

把固定 `EXPOSE` 和 FastAPI CLI `CMD` 替换为：

```dockerfile
CMD ["python", "-m", "app.server"]
```

- [x] **Step 4: 在 Compose 中统一注入并消费监听配置**

在 `api.environment` 中加入：

```yaml
TICKLY_HOST: ${TICKLY_HOST:-0.0.0.0}
TICKLY_PORT: ${TICKLY_PORT:-8000}
```

把 `api.expose` 改为：

```yaml
expose:
  - "${TICKLY_PORT:-8000}"
```

把 API 健康检查改为：

```yaml
healthcheck:
  test: [CMD, python, -c, "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:{}/health'.format(os.environ['TICKLY_PORT']), timeout=2)"]
```

在 `web` 服务中加入：

```yaml
environment:
  TICKLY_API_PORT: ${TICKLY_PORT:-8000}
```

- [x] **Step 5: 让 Caddy 使用 Compose 注入的 API 端口**

把上游改为：

```caddyfile
reverse_proxy api:{$TICKLY_API_PORT}
```

Caddyfile 的 `{$ENV}` 会在配置解析前展开；这里不为变量另设默认值，因为 Compose 始终注入 `TICKLY_API_PORT`。

- [x] **Step 6: 验证 Compose 解析结果包含非默认端口**

Run:

```bash
TICKLY_PORT=18080 TICKLY_JWT_SECRET=verification-only-not-a-secret-123456789 mise exec -- docker compose config
```

Expected: 退出码为 `0`；解析结果中 API 环境、`expose` 和 Web 的 `TICKLY_API_PORT` 均为 `18080`。

- [ ] **Step 7: 运行 Docker smoke 并确认非默认端口闭环通过**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm docker:smoke
```

Expected: PASS with `Docker smoke test passed`。API 容器不发布宿主机端口，Web 仍通过 `127.0.0.1:8080` 接受 smoke 请求。

- [x] **Step 8: 提交容器联动改动**

```bash
git add apps/api/Dockerfile compose.yaml apps/web/Caddyfile scripts/docker-smoke.mjs
git commit -m "feat(docker): 支持配置 API 内部端口"
```

### Task 4: 更新环境示例和使用文档

**Files:**
- Modify: `apps/api/.env.example:1-13`
- Modify: `.env.example:1-3`
- Modify: `README.md:37-49,86-94`

**Interfaces:**
- Consumes: Task 1 与 Task 3 确认的变量名和默认值。
- Produces: 本地开发和 Compose 用户可直接复制的配置示例。

- [x] **Step 1: 更新本地 API 环境示例**

在 `apps/api/.env.example` 的日志配置后加入：

```dotenv
TICKLY_HOST=127.0.0.1
TICKLY_PORT=8000
```

- [x] **Step 2: 更新根 Compose 环境示例**

在根 `.env.example` 加入：

```dotenv
# Compose 内 API 必须监听容器网络可访问的地址，通常保持 0.0.0.0。
TICKLY_HOST=0.0.0.0
TICKLY_PORT=8000
```

- [x] **Step 3: 更新 README 的本地开发说明**

在本地开发命令后说明：

```markdown
API 默认监听 `127.0.0.1:8000`。可在 `apps/api/.env` 中设置
`TICKLY_HOST` 和 `TICKLY_PORT` 覆盖；端口变化后，应同步调整
`VITE_API_PROXY_TARGET`，例如 `http://127.0.0.1:9000`。
```

- [x] **Step 4: 更新 README 的 Docker 说明**

在 Docker 段落补充：

```markdown
Compose 可通过根 `.env` 中的 `TICKLY_HOST`、`TICKLY_PORT` 调整 API
容器的内部监听参数，健康检查和 Caddy 上游端口会同步变化。容器监听地址
通常应保持 `0.0.0.0`；Web 对宿主机发布的端口仍为 `8080`，API 端口不会
直接发布到宿主机。
```

- [x] **Step 5: 检查文档事实和 Markdown**

Run:

```bash
rg -n "TICKLY_HOST|TICKLY_PORT|VITE_API_PROXY_TARGET" apps/api/.env.example .env.example README.md
git diff --check
```

Expected: 两个变量在对应示例与 README 中均有说明，`git diff --check` 退出码为 `0`。

- [x] **Step 6: 提交文档改动**

```bash
git add apps/api/.env.example .env.example README.md
git commit -m "docs: 说明 API 监听配置"
```

### Task 5: 完整验证并复核交付范围

**Files:**
- Verify: `apps/api/app/core/config.py`
- Verify: `apps/api/app/server.py`
- Verify: `compose.yaml`
- Verify: `scripts/docker-smoke.mjs`
- Verify: `README.md`

**Interfaces:**
- Consumes: Task 1 至 Task 4 的全部交付。
- Produces: API 测试、Compose 解析和真实容器闭环的最终证据。

- [x] **Step 1: 运行完整 API 测试**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm test:api
```

Expected: 全部 pytest 测试通过，退出码为 `0`。

- [x] **Step 2: 验证默认 Compose 配置可解析**

Run:

```bash
TICKLY_JWT_SECRET=verification-only-not-a-secret-123456789 mise exec -- docker compose config --quiet
```

Expected: 无输出，退出码为 `0`。

- [ ] **Step 3: 重新运行 Docker smoke**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm docker:smoke
```

Expected: 输出 `Docker smoke test passed`，退出码为 `0`。

- [x] **Step 4: 检查最终差异与工作区状态**

Run:

```bash
git diff --check
git status --short
git log --oneline -5
```

Expected: `git diff --check` 无输出；状态只包含实施计划勾选产生的文档改动，提交历史包含本计划中的功能和文档提交。

- [ ] **Step 5: 在实施计划中勾选完成项并提交验证记录**

更新本文件中实际完成的复选框；任何因 Docker daemon 不可用而未完成的步骤保持未勾选，并在完成说明中如实列出。

```bash
git add docs/superpowers/plans/2026-08-10-configurable-api-listener.md
git commit -m "docs: 记录 API 监听配置实施结果"
```

## 本次执行记录

- API 测试：`157 passed, 1 warning`。
- Compose 默认配置解析：通过；非默认 `TICKLY_PORT=18080` 的配置解析：通过。
- Docker smoke：未完成；本机 Docker daemon 未运行，连接 `unix:///Users/dengrui/.docker/run/docker.sock` 失败。相关 smoke 复跑步骤保持未勾选。
