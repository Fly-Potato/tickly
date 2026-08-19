# Tickly Remote MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个与 Tickly API 同 Compose 部署、供 Codex 通过远程 Streamable HTTP 安全管理 Todo 的独立 MCP Server。

**Architecture:** `apps/mcp` 使用官方 MCP Python SDK v2 提供七个工具，经 Caddy `/mcp` 接收 Codex Bearer Token，再把同一 Token 转发给 `apps/api` 的非公开 `/internal/mcp/v1` 路由。API 独占 SQLite 和任务事务；MCP 只负责协议、鉴权、参数适配、HTTP 调用与安全错误映射。

**Tech Stack:** Python 3.13、uv、MCP Python SDK 2.x、Starlette/Uvicorn、httpx、Pydantic Settings、FastAPI、SQLAlchemy、pytest、Docker Compose、Caddy、Codex MCP config。

---

## 文件结构

新增 MCP 应用：

```text
apps/mcp/
├── .env.example                  # 本地 MCP 配置说明
├── Dockerfile                    # 非 root 生产镜像
├── pyproject.toml                # 独立 Python 项目与测试配置
├── uv.lock                       # 锁定 MCP 依赖
├── app/
│   ├── __init__.py
│   ├── api_client.py             # 唯一的 API HTTP 边界与错误映射
│   ├── auth.py                   # 静态 Bearer 哈希校验与请求 Token 提取
│   ├── config.py                 # MCP 类型化配置
│   ├── errors.py                 # 对 Codex 暴露的稳定安全错误
│   ├── logging.py                # MCP 结构化日志
│   ├── main.py                   # MCPServer、工具和 ASGI 应用工厂
│   ├── middleware.py             # request ID、入口认证和访问日志
│   ├── schemas.py                # 内部 API 响应与工具结构化输出
│   └── server.py                 # Uvicorn 启动入口
└── tests/
    ├── test_api_client.py
    ├── test_auth.py
    ├── test_config.py
    ├── test_http_app.py
    ├── test_logging.py
    ├── test_server.py
    └── test_tools.py
```

API 侧保持现有目录边界，只增加 MCP 专用适配：

```text
apps/api/app/
├── api/
│   ├── mcp_dependencies.py       # 内部路由的 Token 与唯一账号依赖
│   └── routes/mcp_tasks.py       # include_in_schema=False 的内部任务路由
├── schemas/mcp_tasks.py          # serial/parent_serial 内部请求契约
└── services/tasks.py             # 事务内 serial 解析适配
```

不创建 `packages/*`。两个应用只通过 HTTP 契约通信，复制少量边界 Schema 比引入尚无第三个消费者的共享包更符合当前仓库约束。

### Task 1: 建立 MCP 独立项目与严格配置

**Files:**
- Create: `apps/mcp/pyproject.toml`
- Create: `apps/mcp/app/__init__.py`
- Create: `apps/mcp/app/config.py`
- Create: `apps/mcp/tests/test_config.py`
- Create: `apps/mcp/.env.example`
- Modify: `package.json`
- Modify: `.github/workflows/ci.yml`
- Create: `apps/mcp/uv.lock`（由 uv 生成）

- [ ] **Step 1: 写配置失败测试**

```python
# apps/mcp/tests/test_config.py
import pytest
from pydantic import ValidationError

from app.config import Environment, Settings


VALID_HASH = "a" * 64


def test_defaults_are_local_and_use_a_distinct_port() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert str(settings.host) == "127.0.0.1"
    assert settings.port == 8322
    assert str(settings.api_base_url) == "http://127.0.0.1:8321"
    assert settings.token_sha256 is None
    assert settings.allowed_hosts == ["127.0.0.1:*", "localhost:*"]


@pytest.mark.parametrize("value", ["", "abc", "g" * 64, "a" * 63, "a" * 65])
def test_token_hash_must_be_lowercase_sha256(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(token_sha256=value, _env_file=None)


def test_production_requires_token_hash_and_transport_allowlists() -> None:
    with pytest.raises(ValidationError):
        Settings(environment=Environment.PRODUCTION, _env_file=None)

    settings = Settings(
        environment=Environment.PRODUCTION,
        token_sha256=VALID_HASH,
        allowed_hosts=["tickly.example.com"],
        allowed_origins=["https://tickly.example.com"],
        _env_file=None,
    )
    assert settings.token_sha256 is not None


@pytest.mark.parametrize("field", ["connect_timeout_seconds", "request_timeout_seconds"])
def test_timeouts_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0}, _env_file=None)
```

- [ ] **Step 2: 运行测试并确认因应用尚不存在而失败**

Run: `mise exec -- uv run --project apps/mcp pytest apps/mcp/tests/test_config.py -q`

Expected: FAIL，错误包含 `No module named 'app'` 或缺少项目配置。

- [ ] **Step 3: 创建 MCP 项目声明**

```toml
# apps/mcp/pyproject.toml
[project]
name = "tickly-mcp"
version = "0.1.0"
description = "Tickly remote MCP server"
requires-python = ">=3.13"
dependencies = [
    "httpx>=0.28.1,<1",
    "mcp>=2.0.0,<3",
    "pydantic-settings>=2.14.2,<3",
    "uvicorn>=0.35,<1",
]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
    "pytest-asyncio>=1.2,<2",
]

[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 4: 实现类型化配置**

```python
# apps/mcp/app/config.py
from enum import StrEnum
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, IPvAnyAddress, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


MCP_ROOT = Path(__file__).resolve().parents[1]
Port = Annotated[int, Field(ge=1, le=65535)]
PositiveSeconds = Annotated[float, Field(gt=0, le=300)]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TICKLY_MCP_",
        env_file=MCP_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    host: IPvAnyAddress = IPv4Address("127.0.0.1")
    port: Port = 8322
    api_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8321")
    token_sha256: str | None = None
    allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*"]
    allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]
    connect_timeout_seconds: PositiveSeconds = 3
    request_timeout_seconds: PositiveSeconds = 15
    request_id_header: str = "X-Request-ID"
    log_level: LogLevel = "INFO"
    log_json: bool = False
    max_request_body_size: int = Field(default=1_048_576, ge=1024, le=4_194_304)

    @field_validator("token_sha256")
    @classmethod
    def validate_token_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or value != value.lower() or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("token_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.environment is Environment.PRODUCTION:
            if self.token_sha256 is None:
                raise ValueError("production token_sha256 is required")
            if not self.allowed_hosts or not self.allowed_origins:
                raise ValueError("production transport allowlists are required")
        return self
```

- [ ] **Step 5: 增加本地配置样例和根命令**

```dotenv
# apps/mcp/.env.example
TICKLY_MCP_ENVIRONMENT=development
TICKLY_MCP_HOST=127.0.0.1
TICKLY_MCP_PORT=8322
TICKLY_MCP_API_BASE_URL=http://127.0.0.1:8321
TICKLY_MCP_TOKEN_SHA256=replace-with-lowercase-sha256-hex
TICKLY_MCP_ALLOWED_HOSTS=["127.0.0.1:*","localhost:*"]
TICKLY_MCP_ALLOWED_ORIGINS=["http://127.0.0.1:*","http://localhost:*"]
TICKLY_MCP_CONNECT_TIMEOUT_SECONDS=3
TICKLY_MCP_REQUEST_TIMEOUT_SECONDS=15
TICKLY_MCP_LOG_LEVEL=INFO
TICKLY_MCP_LOG_JSON=false
```

在 `package.json` 增加：

```json
"dev:mcp": "cd apps/mcp && uv run python -m app.server --reload",
"test:mcp": "cd apps/mcp && uv run pytest"
```

把根 `check` 改为：

```json
"check": "pnpm lint && pnpm typecheck && pnpm build && pnpm test:web && pnpm test:mcp && pnpm test:api"
```

- [ ] **Step 6: 生成锁文件并更新 CI 安装步骤**

Run: `mise exec -- uv lock --project apps/mcp`

在 `.github/workflows/ci.yml` 的 API 依赖安装之后加入：

```yaml
      - name: 安装 MCP 依赖
        run: mise exec -- uv sync --project apps/mcp --locked
```

- [ ] **Step 7: 运行配置测试**

Run: `mise exec -- pnpm test:mcp -- tests/test_config.py -q`

Expected: PASS。

- [ ] **Step 8: 提交项目基线**

```bash
git add -- apps/mcp/pyproject.toml apps/mcp/uv.lock apps/mcp/.env.example apps/mcp/app/__init__.py apps/mcp/app/config.py apps/mcp/tests/test_config.py package.json .github/workflows/ci.yml
git commit -m "feat(mcp): 建立远程服务项目基线"
```

### Task 2: 为 API 增加 MCP Token 与唯一账号依赖

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/tests/test_config.py`
- Create: `apps/api/app/api/mcp_dependencies.py`
- Create: `apps/api/tests/test_mcp_dependencies.py`

- [ ] **Step 1: 写哈希配置、认证和账号解析失败测试**

```python
# apps/api/tests/test_mcp_dependencies.py
import hashlib

import pytest
from sqlalchemy import select

from app.api.mcp_dependencies import McpAccountUnavailable, McpAuthenticationRequired, resolve_mcp_user, verify_mcp_token
from app.core.config import Settings
from app.models import User


RAW_TOKEN = "test-mcp-token"
TOKEN_HASH = hashlib.sha256(RAW_TOKEN.encode()).hexdigest()


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database_url = f"sqlite:///{tmp_path / 'mcp-dependencies.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    settings = Settings(database_url=database_url, _env_file=None)
    engine = create_engine_for_settings(settings)
    factory = create_session_factory(engine)
    with factory() as database_session:
        create_account(database_session, "potato", "correct horse battery staple")
        yield database_session
    engine.dispose()


def test_verify_mcp_token_accepts_only_matching_bearer() -> None:
    settings = Settings(mcp_token_sha256=TOKEN_HASH, _env_file=None)
    verify_mcp_token(RAW_TOKEN, settings)
    with pytest.raises(McpAuthenticationRequired):
        verify_mcp_token("wrong", settings)


def test_verify_mcp_token_fails_closed_without_configuration() -> None:
    with pytest.raises(McpAuthenticationRequired):
        verify_mcp_token(RAW_TOKEN, Settings(_env_file=None))


def test_resolve_mcp_user_requires_exactly_one_active_account(session) -> None:
    user = session.scalar(select(User))
    assert user is not None
    assert resolve_mcp_user(session).id == user.id

    user.is_active = False
    session.commit()
    with pytest.raises(McpAccountUnavailable):
        resolve_mcp_user(session)
```

该测试文件导入 `Iterator`、`Path`、Alembic `command/Config`、SQLAlchemy
`Session`、`create_engine_for_settings`、`create_session_factory` 和
`create_account`。第二账号测试直接向 Session 添加另一个 `User` 并提交，随后断言
`resolve_mcp_user` 抛出 `McpAccountUnavailable`；测试注释和断言说明使用中文。

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `mise exec -- uv --directory apps/api run pytest tests/test_mcp_dependencies.py tests/test_config.py -q`

Expected: FAIL，缺少 `mcp_dependencies` 或 `mcp_token_sha256`。

- [ ] **Step 3: 增加 API 可选哈希配置**

在 `Settings` 增加：

```python
    mcp_token_sha256: str | None = None

    @field_validator("mcp_token_sha256")
    @classmethod
    def validate_mcp_token_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or value != value.lower() or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("mcp_token_sha256 must be a lowercase SHA-256 hex digest")
        return value
```

API 不因缺少该值而拒绝普通启动；只有 MCP 内部依赖失败关闭。

- [ ] **Step 4: 实现常量时间校验和唯一账号解析**

```python
# apps/api/app/api/mcp_dependencies.py
import hashlib
import secrets

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.models import User


class McpAuthenticationRequired(Exception):
    """MCP Token 缺失、配置不可用或校验失败。"""


class McpAccountUnavailable(Exception):
    """数据库不能唯一解析到一个启用账号。"""


def verify_mcp_token(token: str, settings: Settings) -> None:
    expected = settings.mcp_token_sha256
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if expected is None or not secrets.compare_digest(actual, expected):
        raise McpAuthenticationRequired


def resolve_mcp_user(session: Session) -> User:
    users = list(session.scalars(select(User).limit(2)).all())
    if len(users) != 1 or not users[0].is_active:
        raise McpAccountUnavailable
    return users[0]


_bearer = HTTPBearer(auto_error=False)


def get_mcp_current_user(request: Request, session: Session, credentials: HTTPAuthorizationCredentials | None) -> User:
    if credentials is None:
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="需要 MCP 认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        verify_mcp_token(credentials.credentials, request.app.state.settings)
        return resolve_mcp_user(session)
    except McpAuthenticationRequired as error:
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="需要 MCP 认证",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except McpAccountUnavailable as error:
        raise AppError(
            status_code=503,
            code="mcp_account_unavailable",
            message="MCP 账号不可用",
        ) from error
```

实际 FastAPI dependency 使用以下组合，复用现有 `get_db_session`，但不要让普通
`CurrentUser` 接受 MCP Token：

```python
from typing import Annotated

from fastapi import Depends

from app.api.dependencies import DbSession


McpBearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(_bearer),
]


def get_mcp_current_user(
    request: Request,
    session: DbSession,
    credentials: McpBearerCredentials,
) -> User:
    if credentials is None:
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="需要 MCP 认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        verify_mcp_token(credentials.credentials, request.app.state.settings)
        return resolve_mcp_user(session)
    except McpAuthenticationRequired as error:
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="需要 MCP 认证",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except McpAccountUnavailable as error:
        raise AppError(
            status_code=503,
            code="mcp_account_unavailable",
            message="MCP 账号不可用",
        ) from error


McpCurrentUser = Annotated[User, Depends(get_mcp_current_user)]
```

- [ ] **Step 5: 补齐配置与依赖测试并运行**

Run: `mise exec -- uv --directory apps/api run pytest tests/test_mcp_dependencies.py tests/test_config.py -q`

Expected: PASS。

- [ ] **Step 6: 提交认证边界**

```bash
git add -- apps/api/app/core/config.py apps/api/app/api/mcp_dependencies.py apps/api/tests/test_config.py apps/api/tests/test_mcp_dependencies.py
git commit -m "feat(api): 增加MCP内部认证依赖"
```

### Task 3: 实现 API 内部只读 serial 契约

**Files:**
- Create: `apps/api/app/schemas/mcp_tasks.py`
- Create: `apps/api/app/api/routes/mcp_tasks.py`
- Modify: `apps/api/app/services/tasks.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/test_mcp_tasks_api.py`

- [ ] **Step 1: 写内部路由认证、serial 查询和 OpenAPI 隐藏测试**

```python
def test_internal_routes_require_mcp_token(mcp_client) -> None:
    response = mcp_client.get("/internal/mcp/v1/tasks")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_mcp_token_cannot_access_public_task_api(mcp_client, mcp_headers) -> None:
    response = mcp_client.get("/api/v1/tasks", headers=mcp_headers)
    assert response.status_code == 401


def test_internal_detail_resolves_owned_task_by_serial(mcp_client, mcp_headers, owned_task) -> None:
    response = mcp_client.get(f"/internal/mcp/v1/tasks/{owned_task.serial}", headers=mcp_headers)
    assert response.status_code == 200
    assert response.json()["serial"] == owned_task.serial


def test_internal_routes_are_absent_from_public_openapi(mcp_client) -> None:
    paths = mcp_client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/internal/mcp/") for path in paths)
```

同文件增加：列表保留根任务组、cursor、主题和父候选语义；另一账号相同 serial 不可见；不存在 serial 返回统一 `task_not_found`。

- [ ] **Step 2: 运行只读路由测试并确认 404/导入失败**

Run: `mise exec -- uv --directory apps/api run pytest tests/test_mcp_tasks_api.py -q`

Expected: FAIL，内部路由尚不存在。

- [ ] **Step 3: 增加 serial 查询服务函数**

在 `apps/api/app/services/tasks.py` 增加：

```python
def get_task_by_serial(session: Session, user_id: str, serial: int) -> Task:
    """只在当前账号范围内按稳定流水号读取任务。"""
    task = session.scalar(select(Task).where(Task.user_id == user_id, Task.serial == serial))
    if task is None:
        raise TaskNotFound
    return task


def get_task_detail_by_serial(session: Session, user_id: str, serial: int) -> TaskDetail:
    """按账号流水号读取任务及其直接子任务。"""
    task = get_task_by_serial(session, user_id, serial)
    children = list(
        session.scalars(
            select(Task)
            .where(Task.user_id == user_id, Task.parent_id == task.id)
            .order_by(Task.serial.asc())
        ).all()
    )
    return TaskDetail(task=task, children=children)
```

- [ ] **Step 4: 创建 MCP 内部 Schema 与只读 router**

`apps/api/app/schemas/mcp_tasks.py` 复用现有响应类型，并定义：

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpParentOptionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value
```

`mcp_tasks.py` 使用 `APIRouter(prefix="/internal/mcp/v1/tasks", include_in_schema=False)`，注册 GET 列表、topics、parent-options 和 `/{serial}`。列表直接复用 `TaskListQuery`、`list_tasks` 和现有响应组装；详情调用 `get_task_detail_by_serial`。
固定路由必须先于 `/{serial}` 注册；路径流水号使用
`Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)]`，避免超出 SQLite
INTEGER 范围的值进入驱动层。

- [ ] **Step 5: 在应用工厂挂载内部 router**

```python
from app.api.routes.mcp_tasks import router as mcp_tasks_router

# 在公开 api_router 之外挂载，避免 api_v1_prefix 和 OpenAPI 暴露语义混合。
application.include_router(mcp_tasks_router)
```

- [ ] **Step 6: 运行只读内部 API 测试和现有任务测试**

Run: `mise exec -- uv --directory apps/api run pytest tests/test_mcp_tasks_api.py tests/test_tasks_api.py tests/test_tasks_service.py -q`

Expected: PASS。

- [ ] **Step 7: 提交只读内部契约**

```bash
git add -- apps/api/app/schemas/mcp_tasks.py apps/api/app/api/routes/mcp_tasks.py apps/api/app/services/tasks.py apps/api/app/main.py apps/api/tests/test_mcp_tasks_api.py
git commit -m "feat(api): 提供MCP内部只读任务契约"
```

### Task 4: 实现 API 内部创建、更新与状态事务

**Files:**
- Modify: `apps/api/app/schemas/mcp_tasks.py`
- Modify: `apps/api/app/services/tasks.py`
- Modify: `apps/api/app/api/routes/mcp_tasks.py`
- Modify: `apps/api/tests/test_mcp_tasks_api.py`
- Modify: `apps/api/tests/test_tasks_service.py`

- [ ] **Step 1: 写 parent_serial、patch null、状态和无 DELETE 测试**

```python
def test_internal_create_resolves_parent_serial_in_same_account(mcp_client, mcp_headers, root_task) -> None:
    response = mcp_client.post(
        "/internal/mcp/v1/tasks",
        headers=mcp_headers,
        json={"title": "子任务", "topic": "工作", "parent_serial": root_task.serial},
    )
    assert response.status_code == 201
    assert response.json()["parent_id"] == root_task.id


def test_internal_patch_distinguishes_omitted_and_null(mcp_client, mcp_headers, owned_task) -> None:
    response = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
        json={"priority": None, "due_at": None, "parent_serial": None},
    )
    assert response.status_code == 200
    assert response.json()["priority"] is None
    assert response.json()["due_at"] is None
    assert response.json()["parent_id"] is None


def test_internal_status_uses_existing_completion_semantics(mcp_client, mcp_headers, owned_task) -> None:
    response = mcp_client.patch(
        f"/internal/mcp/v1/tasks/{owned_task.serial}",
        headers=mcp_headers,
        json={"status": "completed"},
    )
    assert response.status_code == 200
    assert response.json()["completed_at"].endswith("Z")


def test_internal_contract_has_no_delete(mcp_client, mcp_headers, owned_task) -> None:
    response = mcp_client.delete(f"/internal/mcp/v1/tasks/{owned_task.serial}", headers=mcp_headers)
    assert response.status_code == 405
```

再覆盖跨账号 parent serial、二层父级、自身父级、父任务变子任务、失败不消耗 serial，以及 `title`/`description`/`topic` 不能传 null。

- [ ] **Step 2: 运行写入测试并确认失败**

Run: `mise exec -- uv --directory apps/api run pytest tests/test_mcp_tasks_api.py tests/test_tasks_service.py -q`

Expected: FAIL，POST/PATCH 尚未注册。

- [ ] **Step 3: 定义 serial 写入 Schema**

```python
class McpTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    topic: str = Field(min_length=1, max_length=100)
    due_at: datetime | None = None
    parent_serial: int | None = Field(default=None, ge=1, le=9_223_372_036_854_775_807)


class McpTaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: TaskPriority | None = None
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    status: TaskStatus | None = None
    due_at: datetime | None = None
    parent_serial: int | None = Field(default=None, ge=1, le=9_223_372_036_854_775_807)
```

为两个 Schema 加入与公开契约一致的明确校验：

```python
    @field_validator("title", "topic", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return value.astimezone(UTC)
```

`McpTaskCreateRequest` 的 after validator 在 `description is None` 时写入 title。
`McpTaskUpdateRequest` 的 after validator 要求 `model_fields_set` 非空，并拒绝显式
把 `title`、`description`、`topic`、`status` 设为 null。nullable 字段仍依赖
`model_fields_set` 表达显式清空。

- [ ] **Step 4: 在事务锁内解析 parent serial**

新增私有函数：

```python
def _require_valid_parent_serial(
    session: Session,
    user_id: str,
    parent_serial: int,
    *,
    task_id: str | None = None,
) -> Task:
    parent = session.scalar(
        select(Task).where(Task.user_id == user_id, Task.serial == parent_serial)
    )
    if parent is None or parent.parent_id is not None or parent.id == task_id:
        raise InvalidTaskRelationship
    return parent
```

实现 `create_task_by_serial`：先调用 `_allocate_serial` 获取账号写锁，再解析父流水号、构造 Task、提交；任一失败统一 rollback。实现 `update_task_by_serial`：当 patch 包含 `parent_serial` 时先 `_lock_user_for_task_relationship`，随后重新读取目标与父任务，在同一事务复用现有字段更新和父任务不能降级为子任务的校验。

- [ ] **Step 5: 注册内部 POST/PATCH 路由**

POST 返回 `201 + TaskResponse`，PATCH 返回 `TaskResponse`；映射继续使用 `task_not_found` 和 `invalid_task_relationship`，不增加 DELETE。

- [ ] **Step 6: 运行 MCP 内部 API、任务服务和公开 API 回归测试**

Run: `mise exec -- uv --directory apps/api run pytest tests/test_mcp_tasks_api.py tests/test_tasks_service.py tests/test_tasks_api.py -q`

Expected: PASS。

- [ ] **Step 7: 提交写入契约**

```bash
git add -- apps/api/app/schemas/mcp_tasks.py apps/api/app/services/tasks.py apps/api/app/api/routes/mcp_tasks.py apps/api/tests/test_mcp_tasks_api.py apps/api/tests/test_tasks_service.py
git commit -m "feat(api): 支持MCP内部任务写入"
```

### Task 5: 实现 MCP API Client 与安全错误映射

**Files:**
- Create: `apps/mcp/app/errors.py`
- Create: `apps/mcp/app/schemas.py`
- Create: `apps/mcp/app/api_client.py`
- Create: `apps/mcp/tests/test_api_client.py`

- [ ] **Step 1: 写 HTTP 参数、Token、request ID、无重试和错误映射测试**

```python
@pytest.mark.asyncio
async def test_list_tasks_forwards_query_and_security_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api:8321") as http:
        client = TicklyApiClient(http, max_response_bytes=1_048_576)
        result = await client.list_tasks(
            token="raw-token",
            request_id="request-1",
            status="all",
            topic=None,
            sort="created_at",
            order="desc",
            cursor=None,
            limit=50,
        )

    assert result.next_cursor is None
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer raw-token"
    assert requests[0].headers["X-Request-ID"] == "request-1"
```

增加参数化用例验证 `401`、`404`、已知 `422`、`503 mcp_account_unavailable`、超时、非 JSON、未知状态码和超过响应上限时分别得到稳定 `McpToolError`，且 mock 调用次数始终为 1。

- [ ] **Step 2: 运行 client 测试并确认失败**

Run: `mise exec -- pnpm test:mcp -- tests/test_api_client.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 定义安全错误类型和响应模型**

```python
# apps/mcp/app/errors.py
class McpToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.public_message = message
```

`schemas.py` 用 Pydantic 定义 `TaskPayload`、`TaskGroupPayload`、`TaskListPayload`、`TaskDetailPayload`、`TopicListPayload` 和 `ParentOptionPagePayload`，字段与 API 响应完全一致，时间字段使用 aware `datetime`。

- [ ] **Step 4: 实现单一 HTTP 请求边界**

```python
class TicklyApiClient:
    def __init__(self, http: httpx.AsyncClient, *, max_response_bytes: int) -> None:
        self._http = http
        self._max_response_bytes = max_response_bytes

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        request_id: str,
        params: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._http.request(
                method,
                path,
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": request_id},
                params=params,
                json=json,
            )
        except httpx.TimeoutException as error:
            raise McpToolError("upstream_unavailable", "Tickly API 暂时不可用") from error
        except httpx.RequestError as error:
            raise McpToolError("upstream_unavailable", "Tickly API 暂时不可用") from error

        if len(response.content) > self._max_response_bytes:
            raise McpToolError("upstream_contract_error", "Tickly API 返回了无效响应")
        return self._decode_response(response)
```

`_decode_response` 使用固定 allowlist，不得把 URL、响应正文或底层异常文本放入
`McpToolError`：

```python
    def _decode_response(self, response: httpx.Response) -> dict[str, object]:
        try:
            body = response.json()
        except ValueError as error:
            raise McpToolError("upstream_contract_error", "Tickly API 返回了无效响应") from error
        if not isinstance(body, dict):
            raise McpToolError("upstream_contract_error", "Tickly API 返回了无效响应")
        if 200 <= response.status_code < 300:
            return body

        error_body = body.get("error")
        code = error_body.get("code") if isinstance(error_body, dict) else None
        known_messages = {
            "authentication_required": "需要 MCP 认证",
            "mcp_account_unavailable": "MCP 账号不可用",
            "task_not_found": "任务不存在",
            "invalid_cursor": "分页游标无效",
            "invalid_task_relationship": "父待办关系无效",
            "validation_error": "请求参数无效",
        }
        if isinstance(code, str) and code in known_messages:
            raise McpToolError(code, known_messages[code])
        if response.status_code >= 500:
            raise McpToolError("upstream_unavailable", "Tickly API 暂时不可用")
        raise McpToolError("upstream_contract_error", "Tickly API 返回了无效响应")
```

- [ ] **Step 5: 为六类 API 操作增加类型化方法**

实现 `list_tasks`、`get_task`、`list_topics`、`find_parent_tasks`、`create_task`、
`update_task`。每个方法只调用一次 `_request`，并用对应 Pydantic 模型
`model_validate`；捕获 `ValidationError` 后固定映射为
`McpToolError("upstream_contract_error", "Tickly API 返回了无效响应")`。

- [ ] **Step 6: 运行 API Client 测试**

Run: `mise exec -- pnpm test:mcp -- tests/test_api_client.py -q`

Expected: PASS。

- [ ] **Step 7: 提交 HTTP 边界**

```bash
git add -- apps/mcp/app/errors.py apps/mcp/app/schemas.py apps/mcp/app/api_client.py apps/mcp/tests/test_api_client.py
git commit -m "feat(mcp): 增加Tickly API客户端"
```

### Task 6: 建立 MCP HTTP 入口、Bearer 防线和生命周期

**Files:**
- Create: `apps/mcp/app/auth.py`
- Create: `apps/mcp/app/middleware.py`
- Create: `apps/mcp/app/main.py`
- Create: `apps/mcp/app/server.py`
- Create: `apps/mcp/tests/test_auth.py`
- Create: `apps/mcp/tests/test_http_app.py`
- Create: `apps/mcp/tests/test_server.py`

- [ ] **Step 1: 写 Bearer、health、ready、Origin/Host 和启动参数测试**

```python
def test_mcp_endpoint_rejects_missing_and_wrong_bearer(http_client) -> None:
    missing = http_client.post("/mcp", json={})
    wrong = http_client.post("/mcp", headers={"Authorization": "Bearer wrong"}, json={})
    assert missing.status_code == wrong.status_code == 401
    assert missing.headers["WWW-Authenticate"].startswith("Bearer")


def test_health_is_public_but_not_an_mcp_protocol_route(http_client) -> None:
    response = http_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_request_id_is_returned_on_authentication_failure(http_client) -> None:
    response = http_client.post("/mcp", headers={"X-Request-ID": "mcp-request-1"}, json={})
    assert response.headers["X-Request-ID"] == "mcp-request-1"
```

增加正确 Bearer 可以进入 MCP 协议层、错误 Host/Origin 被拒绝、`/ready` 在 API 不可达时为 503，以及 server 把 host/port/reload 传给 Uvicorn 的测试。

- [ ] **Step 2: 运行入口测试并确认失败**

Run: `mise exec -- pnpm test:mcp -- tests/test_auth.py tests/test_http_app.py tests/test_server.py -q`

Expected: FAIL，入口模块尚不存在。

- [ ] **Step 3: 实现静态 Bearer 校验**

```python
# apps/mcp/app/auth.py
import hashlib
import secrets


def bearer_matches(token: str, expected_sha256: str | None) -> bool:
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return expected_sha256 is not None and secrets.compare_digest(actual, expected_sha256)


def token_from_authorization(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token
```

- [ ] **Step 4: 实现 request ID 与 Bearer ASGI 中间件**

`RequestIdMiddleware` 复用 API 的合法字符规则，但要把生成或规范化后的 header 写回 request `scope["headers"]`，保证 SDK `Context.headers` 能读取同一个值。`StaticBearerMiddleware` 仅跳过 `/health` 和 `/ready`；其他路径在进入 SDK 前校验并返回固定 `401` JSON，绝不回显 Token。

- [ ] **Step 5: 创建 MCPServer 和 ASGI 工厂**

```python
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings


INSTRUCTIONS = (
    "Tickly MCP 仅管理当前账号的 Todo。引用不明确时先调用只读工具确认 serial；"
    "写入必须遵循 Codex 审批策略。服务不提供删除能力，不得把内部 UUID 当作用户输入。"
)


def create_mcp_server(
    settings: Settings,
    *,
    api_client_override: TicklyApiClient | None = None,
) -> MCPServer:
    server = MCPServer(
        name="tickly",
        title="Tickly Todo",
        description="读取和管理 Tickly Todo",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        lifespan=build_lifespan(settings, api_client_override),
    )
    register_health_routes(server, settings)
    return server
```

`build_lifespan` 创建一个带固定 base URL 和 connect/read/write/pool timeout 的 `httpx.AsyncClient`，yield 包含 `TicklyApiClient` 的 dataclass，并在退出时关闭连接。

`create_http_app` 调用 `server.streamable_http_app(streamable_http_path="/mcp", max_request_body_size=settings.max_request_body_size, transport_security=TransportSecuritySettings(...), host=str(settings.host))`，再按 `RequestIdMiddleware(StaticBearerMiddleware(app))` 顺序包装。health/ready 使用 SDK `custom_route`；ready 只调用 API `/ready`，不携带 MCP Token。

- [ ] **Step 6: 实现统一 Uvicorn 入口**

```python
def run_server(*, reload: bool = False) -> None:
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=str(settings.host),
        port=settings.port,
        reload=reload,
    )
```

CLI 只接受 `--reload`，生产容器不传该标志。

- [ ] **Step 7: 运行 HTTP 入口测试**

Run: `mise exec -- pnpm test:mcp -- tests/test_auth.py tests/test_http_app.py tests/test_server.py -q`

Expected: PASS。

- [ ] **Step 8: 提交协议入口**

```bash
git add -- apps/mcp/app/auth.py apps/mcp/app/middleware.py apps/mcp/app/main.py apps/mcp/app/server.py apps/mcp/tests/test_auth.py apps/mcp/tests/test_http_app.py apps/mcp/tests/test_server.py
git commit -m "feat(mcp): 建立安全HTTP协议入口"
```

### Task 7: 注册四个只读工具

**Files:**
- Create: `apps/mcp/app/tools.py`
- Modify: `apps/mcp/app/schemas.py`
- Modify: `apps/mcp/app/main.py`
- Create: `apps/mcp/tests/test_tools.py`

- [ ] **Step 1: 写工具清单、Schema、注解和调用测试**

```python
@pytest.mark.asyncio
async def test_server_exposes_exact_read_tools(fake_api_client) -> None:
    server = create_mcp_server(
        Settings(environment=Environment.TEST, token_sha256="a" * 64, _env_file=None),
        api_client_override=fake_api_client,
        security_context_provider=lambda context: ("test-token", "test-request"),
    )
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert {"list_tasks", "get_task", "list_topics", "find_parent_tasks"} <= set(tools)
    for name in ("list_tasks", "get_task", "list_topics", "find_parent_tasks"):
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.idempotent_hint is True
        assert tools[name].annotations.open_world_hint is False
```

增加默认筛选、cursor、serial、标题/`#serial` 父候选查询、结构化输出 `summary` 字段和 `Context.headers` Token/request ID 透传测试。

- [ ] **Step 2: 运行工具测试并确认失败**

Run: `mise exec -- pnpm test:mcp -- tests/test_tools.py -q`

Expected: FAIL，工具尚未注册。

- [ ] **Step 3: 定义只读输出模型**

在 `schemas.py` 增加：

```python
class TaskListResult(BaseModel):
    summary: str
    items: list[TaskGroupPayload]
    next_cursor: str | None


class TaskDetailResult(BaseModel):
    summary: str
    task: TaskPayload
    children: list[TaskPayload]


class TopicListResult(BaseModel):
    summary: str
    items: list[str]


class ParentOptionResult(BaseModel):
    summary: str
    items: list[ParentOptionPayload]
    next_cursor: str | None


TaskSerial = Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807)]
```

- [ ] **Step 4: 注册只读工具与准确注解**

在 `tools.py` 定义
`SecurityContextProvider = Callable[[Context], tuple[str, str]]`，并让
`register_tools(server, provider)` 注册本任务的四个工具。把 `create_mcp_server`
扩展为可选参数
`security_context_provider: SecurityContextProvider = request_security_context`，在 health
路由注册之后调用 `register_tools(server, security_context_provider)`。生产默认实现读取
已验证请求 headers，单元测试注入固定 `("test-token", "test-request")`，从而不伪造
SDK 私有 request context；HTTP 入口测试仍覆盖真实默认实现。

```python
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@server.tool(annotations=READ_ONLY, structured_output=True)
async def get_task(serial: TaskSerial, ctx: Context) -> TaskDetailResult:
    """按账号流水号读取一个任务及其直接子任务。"""
    token, request_id = request_security_context(ctx)
    payload = await api_client_from(ctx).get_task(token=token, request_id=request_id, serial=serial)
    return TaskDetailResult(
        summary=f"已读取任务 #{payload.serial}",
        task=payload,
        children=payload.children,
    )
```

`list_tasks` 把六个查询字段逐项传给 client，并用返回页构造
`TaskListResult(summary=f"已读取 {len(payload.items)} 个任务组", ...)`。
`list_topics` 无输入，summary 使用返回主题数量。`find_parent_tasks` 逐项传递
`query/cursor/limit`，summary 使用候选数量。`request_security_context` 只解析已经
由入口验证的 Authorization 与 request ID；异常统一抛出
`McpToolError("authentication_required", "需要 MCP 认证")`，不得回显 header。

- [ ] **Step 5: 运行只读工具测试**

Run: `mise exec -- pnpm test:mcp -- tests/test_tools.py -q`

Expected: PASS。

- [ ] **Step 6: 提交只读工具**

```bash
git add -- apps/mcp/app/tools.py apps/mcp/app/schemas.py apps/mcp/app/main.py apps/mcp/tests/test_tools.py
git commit -m "feat(mcp): 提供任务只读工具"
```

### Task 8: 注册三个无删除写工具

**Files:**
- Modify: `apps/mcp/app/tools.py`
- Modify: `apps/mcp/app/schemas.py`
- Modify: `apps/mcp/tests/test_tools.py`

- [ ] **Step 1: 写创建、patch、状态、注解和无删除测试**

```python
@pytest.mark.asyncio
async def test_server_exposes_exact_seven_tools_without_delete(fake_api_client) -> None:
    server = create_mcp_server(
        Settings(environment=Environment.TEST, token_sha256="a" * 64, _env_file=None),
        api_client_override=fake_api_client,
        security_context_provider=lambda context: ("test-token", "test-request"),
    )
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == {
        "list_tasks",
        "get_task",
        "list_topics",
        "find_parent_tasks",
        "create_task",
        "update_task",
        "set_task_status",
    }
    for name in ("create_task", "update_task", "set_task_status"):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is False
        assert tools[name].annotations.idempotent_hint is False
        assert tools[name].annotations.open_world_hint is False
```

增加 `create_task` 不接受 status/server 字段、`update_task` 不接受 status、nullable 字段显式清空、`set_task_status` 只接受三个枚举值、aware datetime 和 `parent_serial` 的测试。

- [ ] **Step 2: 运行写工具测试并确认失败**

Run: `mise exec -- pnpm test:mcp -- tests/test_tools.py -q`

Expected: FAIL，只有四个工具。

- [ ] **Step 3: 定义工具输入与写入结果模型**

```python
class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskWriteResult(BaseModel):
    summary: str
    task: TaskPayload
```

`CreateTaskInput` 与 `UpdateTaskInput` 使用严格 Pydantic model；update 依赖 `model_fields_set` 区分省略和 null，状态不进入 update model。

- [ ] **Step 4: 注册写工具**

```python
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


@server.tool(annotations=WRITE, structured_output=True)
async def set_task_status(serial: TaskSerial, status: TaskStatus, ctx: Context) -> TaskWriteResult:
    """把任务切换为 New、In Progress 或 Completed。"""
    token, request_id = request_security_context(ctx)
    task = await api_client_from(ctx).update_task(
        token=token,
        request_id=request_id,
        serial=serial,
        patch={"status": status.value},
    )
    return TaskWriteResult(summary=f"已更新任务 #{serial} 的状态", task=task)
```

`create_task` 和 `update_task` 按输入 model 的 `model_dump(exclude_unset=True, mode="json")` 转发；不得从空值或自然语言猜测未提供字段。

- [ ] **Step 5: 运行完整工具测试**

Run: `mise exec -- pnpm test:mcp -- tests/test_tools.py -q`

Expected: PASS，工具数恰好为 7。

- [ ] **Step 6: 提交写工具**

```bash
git add -- apps/mcp/app/tools.py apps/mcp/app/schemas.py apps/mcp/tests/test_tools.py
git commit -m "feat(mcp): 支持受限任务写入工具"
```

### Task 9: 增加安全结构化日志与敏感信息回归

**Files:**
- Create: `apps/mcp/app/logging.py`
- Modify: `apps/mcp/app/middleware.py`
- Modify: `apps/mcp/app/main.py`
- Create: `apps/mcp/tests/test_logging.py`

- [ ] **Step 1: 写日志字段与敏感数据排除测试**

```python
def test_json_log_contains_only_safe_access_fields() -> None:
    record = logging.LogRecord(
        name="tickly.mcp.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="request.completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "request-1"
    record.method = "POST"
    record.path = "/mcp"
    record.status = 200
    record.duration_ms = 1.25
    payload = json.loads(JsonFormatter().format(record))
    assert set(payload) == {"timestamp", "level", "logger", "message", "request_id", "method", "path", "status", "duration_ms"}
```

用 `caplog` 调用含 `Authorization: Bearer secret-token`、任务标题和描述的成功/失败工具，断言日志文本不含 Token、哈希、任务正文、请求 JSON 和内部 API URL。

- [ ] **Step 2: 运行日志测试并确认失败**

Run: `mise exec -- pnpm test:mcp -- tests/test_logging.py -q`

Expected: FAIL，formatter 尚不存在。

- [ ] **Step 3: 实现 MCP JSON formatter 与安全事件字段**

复用 API formatter 的 UTC 时间格式，但 MCP 允许的 extra 字段只包含 `request_id`、`method`、`path`、`status`、`duration_ms`、`tool`、`outcome`、`error_code`。异常边界只记录异常类型和稳定错误码，不调用会把请求/响应正文写入日志的默认 httpx debug handler。

- [ ] **Step 4: 在应用工厂配置日志并运行测试**

Run: `mise exec -- pnpm test:mcp -- tests/test_logging.py tests/test_http_app.py tests/test_tools.py -q`

Expected: PASS。

- [ ] **Step 5: 提交日志边界**

```bash
git add -- apps/mcp/app/logging.py apps/mcp/app/middleware.py apps/mcp/app/main.py apps/mcp/tests/test_logging.py
git commit -m "feat(mcp): 增加安全结构化日志"
```

### Task 10: 接入 Docker Compose 与 Caddy 路由

**Files:**
- Create: `apps/mcp/Dockerfile`
- Modify: `compose.yaml`
- Modify: `apps/web/Caddyfile`
- Modify: `.env.example`
- Modify: `.dockerignore`
- Create: `scripts/check-compose.ps1`

- [ ] **Step 1: 先写 Compose 配置断言脚本或测试**

```powershell
# scripts/check-compose.ps1
$ErrorActionPreference = "Stop"

$rawConfig = docker compose config --format json
if ($LASTEXITCODE -ne 0) {
    throw "docker compose config 执行失败"
}
$config = $rawConfig | ConvertFrom-Json
$serviceNames = @($config.services.PSObject.Properties.Name)

foreach ($requiredService in @("api", "mcp", "web")) {
    if ($requiredService -notin $serviceNames) {
        throw "Compose 缺少服务：$requiredService"
    }
}
if ($null -ne $config.services.api.ports -or $null -ne $config.services.mcp.ports) {
    throw "API 和 MCP 不得发布宿主机端口"
}
if ($null -eq $config.services.web.ports) {
    throw "Web 必须是唯一发布宿主机端口的服务"
}
$apiHash = $config.services.api.environment.TICKLY_MCP_TOKEN_SHA256
$mcpHash = $config.services.mcp.environment.TICKLY_MCP_TOKEN_SHA256
if ([string]::IsNullOrWhiteSpace($apiHash) -or $apiHash -ne $mcpHash) {
    throw "API 与 MCP 必须接收相同 Token 哈希"
}
if ($config.services.mcp.depends_on.api.condition -ne "service_healthy") {
    throw "MCP 必须等待 API healthy"
}
if ($config.services.web.depends_on.mcp.condition -ne "service_healthy") {
    throw "Web 必须等待 MCP healthy"
}
Write-Output "Compose MCP 边界检查通过"
```

- [ ] **Step 2: 运行 Compose 检查并确认缺少 MCP 服务**

Run:

```powershell
$env:TICKLY_JWT_SECRET = "j" * 64
$env:TICKLY_MCP_TOKEN_SHA256 = "a" * 64
$env:TICKLY_MCP_ALLOWED_HOSTS = '["localhost:*"]'
$env:TICKLY_MCP_ALLOWED_ORIGINS = '["http://localhost:*"]'
pwsh -NoProfile -File scripts/check-compose.ps1
```

Expected: FAIL，错误指出缺少 `mcp` 服务。

- [ ] **Step 3: 创建非 root MCP Dockerfile**

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY apps/mcp/pyproject.toml apps/mcp/uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

FROM ghcr.io/astral-sh/uv:python3.13-trixie AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10002 tickly-mcp \
    && useradd --system --uid 10002 --gid tickly-mcp --home-dir /app --shell /usr/sbin/nologin tickly-mcp
WORKDIR /app
COPY --from=builder --chown=tickly-mcp:tickly-mcp /app/.venv /app/.venv
COPY --chown=tickly-mcp:tickly-mcp apps/mcp/app /app/app
USER tickly-mcp
CMD ["python", "-m", "app.server"]
```

- [ ] **Step 4: 增加 Compose MCP 服务和共享哈希**

`api.environment` 增加：

```yaml
      TICKLY_MCP_TOKEN_SHA256: ${TICKLY_MCP_TOKEN_SHA256:?TICKLY_MCP_TOKEN_SHA256 is required}
```

新增：

```yaml
  mcp:
    build:
      context: .
      dockerfile: apps/mcp/Dockerfile
    depends_on:
      api:
        condition: service_healthy
    environment:
      TICKLY_MCP_ENVIRONMENT: production
      TICKLY_MCP_HOST: 0.0.0.0
      TICKLY_MCP_PORT: ${TICKLY_MCP_PORT:-8322}
      TICKLY_MCP_API_BASE_URL: http://api:${TICKLY_PORT:-8321}
      TICKLY_MCP_TOKEN_SHA256: ${TICKLY_MCP_TOKEN_SHA256:?TICKLY_MCP_TOKEN_SHA256 is required}
      TICKLY_MCP_ALLOWED_HOSTS: ${TICKLY_MCP_ALLOWED_HOSTS:?TICKLY_MCP_ALLOWED_HOSTS is required}
      TICKLY_MCP_ALLOWED_ORIGINS: ${TICKLY_MCP_ALLOWED_ORIGINS:?TICKLY_MCP_ALLOWED_ORIGINS is required}
      TICKLY_MCP_LOG_JSON: "true"
    expose:
      - "${TICKLY_MCP_PORT:-8322}"
    healthcheck:
      test: [CMD, python, -c, "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:{}/health'.format(os.environ['TICKLY_MCP_PORT']), timeout=2)"]
      interval: 2s
      timeout: 3s
      retries: 15
      start_period: 5s
```

Web 增加对 MCP healthy 的依赖和 `TICKLY_MCP_PORT` 环境变量。

- [ ] **Step 5: 更新 Caddy 路由顺序**

```caddyfile
@internal path /internal/*
respond @internal 404

@mcp path /mcp /mcp/*
handle @mcp {
	reverse_proxy mcp:{$TICKLY_MCP_PORT}
}
```

该段必须位于静态 SPA fallback 之前；现有 `/api/* /health /ready` 继续代理 API。

- [ ] **Step 6: 更新环境样例和 Docker ignore**

根 `.env.example` 增加不可直接启动的说明性值：

```dotenv
# 原始 Token 只保存在 Codex 主机；这里填写其小写 SHA-256。
TICKLY_MCP_TOKEN_SHA256=replace-with-lowercase-sha256-hex
TICKLY_MCP_PORT=8322
TICKLY_MCP_ALLOWED_HOSTS=["localhost:*","127.0.0.1:*"]
TICKLY_MCP_ALLOWED_ORIGINS=["http://localhost:*","http://127.0.0.1:*"]
```

`.dockerignore` 已允许应用源码和 lock 文件进入 build context；确认没有新增排除规则吞掉 `apps/mcp/app` 或 `apps/mcp/uv.lock`。

- [ ] **Step 7: 运行 Compose 配置检查和镜像构建**

Run:

```powershell
$env:TICKLY_JWT_SECRET = "j" * 64
$env:TICKLY_MCP_TOKEN_SHA256 = "a" * 64
$env:TICKLY_MCP_ALLOWED_HOSTS = '["localhost:*"]'
$env:TICKLY_MCP_ALLOWED_ORIGINS = '["http://localhost:*"]'
pwsh -NoProfile -File scripts/check-compose.ps1
```

Expected: PASS。

Run: `mise exec -- docker compose build api mcp web`

Expected: PASS；镜像构建完成，MCP runtime 使用 uid 10002。

- [ ] **Step 8: 提交部署集成**

```bash
git add -- apps/mcp/Dockerfile compose.yaml apps/web/Caddyfile .env.example .dockerignore scripts/check-compose.ps1
git commit -m "feat(mcp): 接入Compose与Caddy"
```

### Task 11: 更新文档、CI 与运维指引

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/superpowers/specs/2026-08-18-remote-mcp-server-design.md`（仅修正实现中确认的事实）

- [ ] **Step 1: 更新 README 的实际能力与命令**

README 增加：MCP 已实现后的能力描述、`mise exec -- pnpm dev:mcp`、`test:mcp`、七个工具清单、无删除边界、原始 Token/哈希生成、轮换和 Codex 配置。

Codex 示例保持：

```toml
[mcp_servers.tickly]
url = "https://tickly.example.com/mcp"
bearer_token_env_var = "TICKLY_MCP_TOKEN"
default_tools_approval_mode = "writes"
```

明确当前仓库 Compose 默认仍是本机 HTTP `:8080`；真实远程 Codex 连接必须由可信入口终止 TLS，未配置 HTTPS 不属于生产就绪。

- [ ] **Step 2: 更新 AGENTS 验证矩阵**

增加：

```markdown
- MCP 改动：运行 `mise exec -- pnpm test:mcp`；涉及内部任务契约时同时运行 `mise exec -- pnpm test:api`。
- MCP 容器或 Caddy 改动：额外解析 Compose 配置并构建 `api`、`mcp`、`web` 镜像。
```

- [ ] **Step 3: 确认 CI 安装并执行 MCP 测试**

CI 必须在 `pnpm check` 前执行 `uv sync --project apps/mcp --locked`；根 `check` 已包含 `test:mcp`。不要把真实 Token 写入 CI；单元测试使用固定测试哈希。

- [ ] **Step 4: 检查文档事实和 Markdown**

Run: `rg -n "AI 能力尚未实现|MCP|test:mcp|dev:mcp|TICKLY_MCP" README.md AGENTS.md docs/superpowers/specs/2026-08-18-remote-mcp-server-design.md`

Expected: README 不再把 MCP 描述为未实现，但仍明确模型供应商和自然语言草稿尚未实现；命令和配置名与代码一致。

- [ ] **Step 5: 提交文档与 CI**

```bash
git add -- README.md AGENTS.md .github/workflows/ci.yml docs/superpowers/specs/2026-08-18-remote-mcp-server-design.md docs/superpowers/plans/2026-08-18-remote-mcp-server.md
git commit -m "docs(mcp): 补充接入与运维说明"
```

### Task 12: 完整验证与真实 Codex Smoke

**Files:**
- Modify only if a verification failure proves an in-scope defect; add the regression test before the fix.

- [ ] **Step 1: 运行 MCP 全量测试**

Run: `mise exec -- pnpm test:mcp`

Expected: PASS，覆盖配置、Bearer、HTTP client、工具、日志、ASGI 和 server。

- [ ] **Step 2: 运行 API 全量测试**

Run: `mise exec -- pnpm test:api`

Expected: PASS，公开 JWT API 与内部 MCP API 均通过。

- [ ] **Step 3: 运行 Web 与全仓静态检查**

Run: `mise exec -- pnpm lint`

Expected: PASS。

Run: `mise exec -- pnpm typecheck`

Expected: PASS。

Run: `mise exec -- pnpm build`

Expected: PASS。

Run: `mise exec -- pnpm test:web`

Expected: PASS。

- [ ] **Step 4: 检查 Compose 与镜像**

在当前 PowerShell 会话设置长度合规的临时 JWT、测试 Token 哈希、Host 和 Origin allowlist，然后运行：

```powershell
mise exec -- pwsh -File scripts/check-compose.ps1
mise exec -- docker compose build api mcp web
```

Expected: 两条命令均 PASS；若 Docker daemon 不可用，记录为未完成，不得描述为通过。

- [ ] **Step 5: 启动 Compose 并验证公网边界**

Run: `mise exec -- docker compose up --detach api mcp web`

验证：

```text
GET  http://127.0.0.1:8080/health              -> 200 API health
POST http://127.0.0.1:8080/mcp（无 Token）     -> 401
GET  http://127.0.0.1:8080/internal/mcp/v1/tasks -> 404
```

再确认 `docker compose ps` 中三项均 healthy，且只有 Web 暴露宿主机端口。

- [ ] **Step 6: 使用 Codex 完成 HTTPS 手工 smoke**

仅在可信 TLS 入口已经存在时配置 `TICKLY_MCP_TOKEN` 和远程 URL。依次验证：

1. `/mcp` 能列出恰好七个工具。
2. `list_tasks` 和 `get_task` 能按 serial 读取。
3. 经写操作审批创建一个明确标记为 smoke 的任务。
4. 更新字段并切换为 completed。
5. 在 Web 中确认相同数据。
6. 通过 Web 手工删除 smoke 任务，因为 MCP 不提供删除。

Expected: 全部成功，Codex 过程中没有删除工具或绕过审批的写操作。

- [ ] **Step 7: 检查最终 diff 与提交状态**

Run: `git diff --check`

Expected: 无输出。

Run: `git status --short`

Expected: 仅保留用户原有的无关改动；本计划产生的功能文件均已进入前述精确提交。不要 push，除非用户另行要求。

---

## 实施注意事项

- 开始执行前使用 `superpowers:using-git-worktrees` 判断是否需要隔离当前脏工作树。
- 每个功能任务遵循 `superpowers:test-driven-development`，必须先看到预期失败再写实现。
- 每个任务完成后检查 `git status --short` 和 staged names，不能包含用户已有的数据库、环境或并行改动。
- API 与 MCP 都不得记录 Authorization、Token 哈希、任务正文、完整参数或内部 URL。
- 官方 SDK 的 `Context.headers` 是客户端输入；身份仍由入口中间件校验，工具只把已验证请求中的 Token 向内部 API 透传。
- `mcp>=2,<3` 只用于声明兼容线，实际生产版本由 `apps/mcp/uv.lock` 固定；升级 SDK 必须先跑协议和 Codex smoke。
- 当前设计不包含生产 TLS 实现。没有可信 HTTPS 入口时，只能完成本机 Compose 验证，不能完成真实远程生产验收。
