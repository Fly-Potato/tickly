# Tickly 数据库 Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `GET /ready` 实时验证数据库可访问且 Alembic revision 已到 head，同时保持 `/health` 不依赖数据库，并让 Docker smoke 在显式 migration 后验证 readiness。

**Architecture:** 新增与 FastAPI 解耦的数据库 readiness 模块，使用 SQLAlchemy 连接、Alembic `MigrationContext.get_current_heads()` 和 `ScriptDirectory.get_heads()` 比较 revision 集合。应用工厂持有本次应用实际使用的 Engine，路由将数据库异常和 migration mismatch 映射为稳定的 `503`；Docker smoke 在启动服务前显式运行 migration，不把 migration 放进 API 启动流程。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2.x、Alembic、SQLite、pytest、Docker Compose、pnpm、mise。

## Global Constraints

- `GET /health` 必须继续作为不访问数据库和外部服务的基础健康检查。
- API 进程不能因为数据库暂时不可用或 migration 落后而在启动阶段直接退出。
- `GET /ready` 必须在每次请求时实时检查数据库连通性和 Alembic revision，不能缓存结果。
- 应用启动和请求处理过程中不得自动执行 migration，也不得调用 `create_all()`。
- 数据库不可用返回 `503 database_unavailable`；revision 不是最新返回 `503 migration_not_current`。
- 客户端响应不得包含数据库 URL、文件路径、SQL 或底层异常文本。
- 应用只释放自己创建的 Engine；调用方注入的 Engine 由调用方释放。
- 测试使用真实临时文件 SQLite，不把内存数据库作为唯一集成环境。
- 不实现 CLI 账号管理、JWT、认证路由、Todo API 或 Todo Web。
- 测试注释、docstring、fixture 说明和断言说明使用中文；关键资源所有权、异常恢复和 migration 边界使用中文注释。
- 不新增依赖；使用现有 FastAPI、SQLAlchemy、Alembic 和 pytest。

---

### Task 1: 建立独立的数据库 revision 检查核心

**Files:**
- Create: `apps/api/app/db/readiness.py`
- Create: `apps/api/tests/test_readiness.py`

**Interfaces:**
- Consumes: `sqlalchemy.Engine`、`alembic.config.Config`
- Produces: `revisions_are_current(current_heads: Iterable[str], expected_heads: Iterable[str]) -> bool`
- Produces: `database_migration_is_current(database_engine: Engine, alembic_config: Config) -> bool`
- Error behavior: SQLAlchemy 连接或查询异常原样抛出，由 Task 2 的 HTTP 边界统一映射

- [ ] **Step 1: 写入 revision 集合与真实 SQLite 的失败测试**

创建 `apps/api/tests/test_readiness.py`：

```python
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.db.readiness import (
    database_migration_is_current,
    revisions_are_current,
)
from app.db.session import create_engine_for_settings


def make_alembic_config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def make_engine(database_path: Path):
    settings = type(
        "Settings",
        (),
        {"database_url": f"sqlite:///{database_path}"},
    )()
    return create_engine_for_settings(settings)


def test_revision_comparison_is_order_independent() -> None:
    # Alembic 允许多个 heads；readiness 必须比较集合而不是依赖返回顺序。
    assert revisions_are_current(("head_a", "head_b"), ("head_b", "head_a"))
    assert not revisions_are_current(("head_a",), ("head_a", "head_b"))


def test_migrated_database_revision_is_current(tmp_path: Path) -> None:
    database_path = tmp_path / "current.db"
    config = make_alembic_config(database_path)
    command.upgrade(config, "head")
    engine = make_engine(database_path)

    assert database_migration_is_current(engine, config)

    engine.dispose()


def test_empty_database_revision_is_not_current(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.db"
    config = make_alembic_config(database_path)
    engine = make_engine(database_path)

    assert not database_migration_is_current(engine, config)

    engine.dispose()
```

- [ ] **Step 2: 运行新测试并确认因模块缺失而失败**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv run --project apps/api pytest apps/api/tests/test_readiness.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app.db.readiness'`。

- [ ] **Step 3: 实现最小 readiness 核心**

创建 `apps/api/app/db/readiness.py`：

```python
from collections.abc import Iterable

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text


def revisions_are_current(
    current_heads: Iterable[str],
    expected_heads: Iterable[str],
) -> bool:
    """比较数据库与代码中的 migration heads。

    Alembic migration 可能存在多个分支，集合比较可以避免顺序变化导致误判。
    """

    return set(current_heads) == set(expected_heads)


def database_migration_is_current(
    database_engine: Engine,
    alembic_config: Config,
) -> bool:
    """检查数据库可访问性，并判断当前 revisions 是否等于代码 heads。

    连接或查询失败由 SQLAlchemy 异常表达；revision 不一致返回 False。
    本函数只读取状态，不执行 migration，也不修改业务 schema。
    """

    script_directory = ScriptDirectory.from_config(alembic_config)
    with database_engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        migration_context = MigrationContext.configure(connection)
        return revisions_are_current(
            migration_context.get_current_heads(),
            script_directory.get_heads(),
        )
```

- [ ] **Step 4: 运行 readiness 核心测试并确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv run --project apps/api pytest apps/api/tests/test_readiness.py -v
```

Expected: 3 tests PASS。

- [ ] **Step 5: 运行现有数据库与 migration 回归测试**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv run --project apps/api pytest apps/api/tests/test_database.py apps/api/tests/test_migrations.py apps/api/tests/test_models.py -v
```

Expected: 全部 PASS；不得出现新的失败。

- [ ] **Step 6: 提交 readiness 核心**

```bash
git add apps/api/app/db/readiness.py apps/api/tests/test_readiness.py
git commit -m "feat(api): add database revision readiness check"
```

---

### Task 2: 将数据库 readiness 接入应用工厂与 HTTP 路由

**Files:**
- Modify: `apps/api/app/main.py:1-51`
- Modify: `apps/api/app/api/routes/health.py:1-22`
- Modify: `apps/api/tests/test_health.py:1-51`

**Interfaces:**
- Consumes: Task 1 的 `database_migration_is_current(database_engine, alembic_config) -> bool`
- Produces: `create_app(settings: Settings | None = None, *, database_engine: Engine | None = None) -> FastAPI`
- Produces: `application.state.database_engine: Engine`
- Produces: `application.state.owns_database_engine: bool`
- Produces: `application.state.alembic_config: alembic.config.Config`
- HTTP: `GET /ready -> 200 | 503 not_ready | 503 database_unavailable | 503 migration_not_current`

- [ ] **Step 1: 将健康检查测试改为使用隔离数据库并增加失败场景**

用以下内容替换 `apps/api/tests/test_health.py`：

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Environment, Settings
from app.db.session import create_engine_for_settings
from app.main import create_app


def make_settings(database_path: Path) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=f"sqlite:///{database_path}",
        _env_file=None,
    )


def migrate_to_head(database_path: Path) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")


def test_health_does_not_require_database(tmp_path: Path) -> None:
    unavailable_path = tmp_path / "missing" / "tickly.db"
    app = create_app(make_settings(unavailable_path))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    client.close()


def test_ready_requires_lifespan_before_database_check(tmp_path: Path) -> None:
    unavailable_path = tmp_path / "missing" / "tickly.db"
    app = create_app(make_settings(unavailable_path))
    client = TestClient(app)

    response = client.get("/ready", headers={"X-Request-ID": "not-ready"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "not_ready",
            "message": "服务尚未就绪",
            "request_id": "not-ready",
            "details": [],
        }
    }
    client.close()


def test_ready_accepts_database_at_migration_head(tmp_path: Path) -> None:
    database_path = tmp_path / "current.db"
    migrate_to_head(database_path)
    settings = make_settings(database_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    with TestClient(app) as client:
        assert app.state.ready is True
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    assert app.state.ready is False
    # 注入的 Engine 归调用方所有，应用关闭后仍必须可用。
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    engine.dispose()


def test_ready_rejects_database_without_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.db"
    settings = make_settings(database_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    with TestClient(app) as client:
        response = client.get(
            "/ready",
            headers={"X-Request-ID": "migration-behind"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "migration_not_current",
            "message": "数据库迁移版本不是最新",
            "request_id": "migration-behind",
            "details": [],
        }
    }
    engine.dispose()


def test_ready_reports_unavailable_database(tmp_path: Path) -> None:
    unavailable_path = tmp_path / "missing" / "tickly.db"
    settings = make_settings(unavailable_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    with TestClient(app) as client:
        response = client.get(
            "/ready",
            headers={"X-Request-ID": "database-down"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "数据库不可用",
            "request_id": "database-down",
            "details": [],
        }
    }
    assert str(unavailable_path) not in response.text
    engine.dispose()
```

- [ ] **Step 2: 运行健康检查测试并确认新行为尚未实现**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv run --project apps/api pytest apps/api/tests/test_health.py -v
```

Expected: FAIL，至少包含 `create_app() got an unexpected keyword argument 'database_engine'`；测试必须因缺少新接口失败，而不是因测试语法或导入错误失败。

- [ ] **Step 3: 扩展应用工厂的 Engine 注入与所有权**

在 `apps/api/app/main.py` 增加导入：

```python
from alembic.config import Config
from sqlalchemy import Engine

from app.core.config import API_ROOT, Settings
from app.db.session import create_engine_for_settings
```

将 lifespan 的 `finally` 改为：

```python
    finally:
        app.state.ready = False
        if app.state.owns_database_engine:
            # 应用只释放自己创建的 Engine，避免关闭调用方注入的测试资源。
            app.state.database_engine.dispose()
        logger.info("application.stopped")
```

将 `create_app()` 签名和数据库 state 初始化改为：

```python
def create_app(
    settings: Settings | None = None,
    *,
    database_engine: Engine | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings)
    resolved_database_engine = database_engine or create_engine_for_settings(
        resolved_settings
    )
    application = FastAPI(
        title=resolved_settings.app_name,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.ready = False
    application.state.database_engine = resolved_database_engine
    application.state.owns_database_engine = database_engine is None
    application.state.alembic_config = Config(str(API_ROOT / "alembic.ini"))
```

其余 middleware、异常处理器和路由注册保持原顺序。

- [ ] **Step 4: 将 `/ready` 改为同步数据库检查并映射稳定错误**

用以下内容替换 `apps/api/app/api/routes/health.py`：

```python
import logging

from fastapi import APIRouter, Request, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError, request_id_from
from app.db.readiness import database_migration_is_current


logger = logging.getLogger("tickly.readiness")
router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="not_ready",
            message="服务尚未就绪",
        )

    try:
        migration_is_current = database_migration_is_current(
            request.app.state.database_engine,
            request.app.state.alembic_config,
        )
    except SQLAlchemyError as exc:
        # 日志只记录稳定事件名和异常类型，避免泄漏连接 URL、路径或 SQL。
        logger.warning(
            "readiness.database_unavailable",
            extra={
                "request_id": request_id_from(request),
                "error_type": type(exc).__name__,
            },
        )
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="数据库不可用",
        ) from exc

    if not migration_is_current:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="migration_not_current",
            message="数据库迁移版本不是最新",
        )

    return {"status": "ready"}
```

- [ ] **Step 5: 运行健康检查与 readiness 核心测试**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv run --project apps/api pytest apps/api/tests/test_health.py apps/api/tests/test_readiness.py -v
```

Expected: 8 tests PASS。

- [ ] **Step 6: 运行 API 全量测试**

Run:

```bash
UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm test:api
```

Expected: 全部 PASS；现有环境可能继续报告已知的 `StarletteDeprecationWarning`，不得新增其他 warning 或 failure。

- [ ] **Step 7: 提交 FastAPI readiness 接入**

```bash
git add apps/api/app/main.py apps/api/app/api/routes/health.py apps/api/tests/test_health.py
git commit -m "feat(api): make readiness database-aware"
```

---

### Task 3: 保持 Docker smoke 的 migration 与 readiness 顺序正确

**Files:**
- Modify: `apps/api/Dockerfile:14-30`
- Modify: `compose.yaml:1-32`
- Modify: `scripts/docker-smoke.sh:1-23`

**Interfaces:**
- Consumes: Task 2 的数据库感知 `GET /ready`
- Produces: 容器数据库 URL `sqlite:////data/tickly.db`
- Produces: named volume `tickly-data` 挂载到 API 的 `/data`
- Produces: 隔离的 smoke Compose project 与临时 named volume
- Produces: smoke 顺序 `build -> explicit alembic upgrade head -> up -> /ready -> cleanup volumes`
- Constraint: migration 是独立 smoke 步骤，不加入 API `CMD`，也不在应用启动时自动执行

- [ ] **Step 1: 先修改 smoke 脚本，隔离测试 volume 并要求服务启动前显式 migration**

用以下内容替换 `scripts/docker-smoke.sh`：

```sh
#!/bin/sh
set -eu

TICKLY_SMOKE_PROJECT="tickly-smoke-$$"

compose() {
  docker compose --project-name "$TICKLY_SMOKE_PROJECT" "$@"
}

cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM
compose config --quiet
compose build
# migration 必须是独立步骤；API 启动命令不得隐式修改生产 schema。
compose run --rm api alembic upgrade head
compose up --detach

for service in api web; do
  remaining=60
  while [ "$remaining" -gt 0 ]; do
    container_id="$(compose ps --quiet "$service")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    [ "$health" = healthy ] && break
    [ "$health" = unhealthy ] && exit 1
    sleep 1
    remaining=$((remaining - 1))
  done
  [ "$health" = healthy ] || exit 1
done

curl --fail --silent http://127.0.0.1:8080/ | grep -q 'id="root"'
test "$(curl --fail --silent http://127.0.0.1:8080/health)" = '{"status":"ok"}'
test "$(curl --fail --silent http://127.0.0.1:8080/ready)" = '{"status":"ready"}'
printf '%s\n' "Docker smoke test passed"
```

隔离 project 名确保 smoke 清理的 `tickly-data` 只属于本次测试，不会删除开发或生产 Compose project 的数据。

原来的核心顺序：

```sh
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up --detach
```

已由上面的 `compose` helper 执行，所有 Compose 调用必须使用同一个隔离 project。

- [ ] **Step 2: 在 Docker 可用时运行 smoke 并确认当前容器数据库路径导致失败**

Run:

```bash
mise exec -- pnpm docker:smoke
```

Expected when Docker daemon is available: FAIL during migration because当前镜像没有可写且持久化的 `/data` 数据目录。

If Docker daemon is unavailable: 记录 `Cannot connect to the Docker daemon` 原始阻塞信息，继续完成静态 Compose 配置；不得把 smoke 描述为通过。

- [ ] **Step 3: 为 API 镜像创建非 root 可写数据目录**

将 `apps/api/Dockerfile` 的用户创建层改为：

```dockerfile
RUN groupadd --system --gid 10001 tickly \
    && useradd --system --uid 10001 --gid tickly \
        --home-dir /app --shell /usr/sbin/nologin tickly \
    && mkdir --parents /data \
    && chown tickly:tickly /data
```

保持 `USER tickly`、依赖复制和启动命令不变。

- [ ] **Step 4: 给 API 配置固定数据库路径和 named volume**

在 `compose.yaml` 的 `api.environment` 中加入：

```yaml
      TICKLY_DATABASE_URL: sqlite:////data/tickly.db
```

在 `api` 服务中加入：

```yaml
    volumes:
      - tickly-data:/data
```

在文件末尾加入：

```yaml
volumes:
  tickly-data:
```

不要增加自动执行 migration 的 API command，也不要把 API 端口映射到宿主机公网接口。

- [ ] **Step 5: 验证 Compose 配置**

Run:

```bash
docker compose config --quiet
docker compose config
```

Expected: 两条命令退出码均为 0；渲染配置中 `api` 使用 `/data/tickly.db` 且挂载 `tickly-data:/data`。

- [ ] **Step 6: 在 Docker 可用时重新运行 smoke**

Run:

```bash
mise exec -- pnpm docker:smoke
```

Expected when Docker daemon is available: PASS，并输出 `Docker smoke test passed`；migration 先完成，随后 `/ready` 返回 `{"status":"ready"}`。

If Docker daemon is unavailable: 保留未验证状态，并在最终说明中明确只有 Compose config 通过。

- [ ] **Step 7: 提交 Docker readiness 顺序**

```bash
git add apps/api/Dockerfile compose.yaml scripts/docker-smoke.sh
git commit -m "build: migrate database before readiness smoke"
```

---

### Task 4: 更新真实仓库状态并完成全量验证

**Files:**
- Modify: `AGENTS.md:7-11`
- Modify: `docs/roadmaps/2026-07-26-tickly-zero-to-one.md:22-46`

**Interfaces:**
- Consumes: Tasks 1-3 的已实现行为
- Produces: 准确区分已实现数据库/readiness 基线与未实现认证、Todo、AI、备份部署的中文文档

- [ ] **Step 1: 更新 AGENTS.md 当前 API 状态**

将 `AGENTS.md` 当前状态中的 API 条目改为：

```markdown
- `apps/api` 已具备 FastAPI 应用工厂、`/health`、数据库与 migration 感知的 `/ready`、请求 ID、统一错误、结构化日志、SQLAlchemy/SQLite 数据层和首份 Alembic migration；Todo 与登录流程尚未实现。
```

- [ ] **Step 2: 更新路线图的当前仓库状态**

将路线图“当前仓库状态”中的目录说明改为：

```text
tickly/
├── apps/
│   ├── web/                 # 基础 React/Vite 页面
│   └── api/                 # FastAPI 工程基线与 SQLite 数据层
├── packages/                # 共享包预留目录
├── docs/
├── compose.yaml
├── package.json
├── pnpm-workspace.yaml
└── mise.toml
```

将“当前尚未实现”列表改为：

```markdown
- 认证和 Todo 业务 API。
- CLI 账号与密码管理。
- Todo 页面与业务交互。
- Web 自动化测试框架。
- AI 供应商集成。
- VPS HTTPS、备份、恢复和完整发布流程。
```

删除已经过时的 Web lint 基线错误描述，补充：

```markdown
阶段 0 的工程与 Docker 骨架已建立；阶段 1 已具备 SQLAlchemy、SQLite、ORM 模型、Alembic migration 与数据库 readiness 检查。Docker 镜像和 Compose smoke 是否通过必须以当次 Docker daemon 验证结果为准。
```

- [ ] **Step 3: 检查文档事实与 Markdown**

Run:

```bash
rg -n "仅有 GET /health|数据库、ORM 与 migration|现有 Web lint" AGENTS.md docs/roadmaps/2026-07-26-tickly-zero-to-one.md
git diff --check
```

Expected: `rg` 无匹配；`git diff --check` 退出码为 0。

- [ ] **Step 4: 运行完整仓库检查**

Run:

```bash
CI=true UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm check
```

Expected: Web lint、typecheck、build 全部 PASS；API pytest 全部 PASS。

- [ ] **Step 5: 再次验证 Compose 配置**

Run:

```bash
docker compose config --quiet
```

Expected: PASS。

- [ ] **Step 6: 核对最终变更范围**

Run:

```bash
git status --short
git diff --stat
git diff -- AGENTS.md docs/roadmaps/2026-07-26-tickly-zero-to-one.md
```

Expected: 只包含本任务尚未提交的两份文档；不包含密钥、`.env`、数据库文件、缓存或构建产物。

- [ ] **Step 7: 提交现状文档**

```bash
git add AGENTS.md docs/roadmaps/2026-07-26-tickly-zero-to-one.md
git commit -m "docs: record database readiness baseline"
```

- [ ] **Step 8: 验证提交后的工作区和提交序列**

Run:

```bash
git status --short --branch
git log -5 --oneline
```

Expected: 工作区干净；提交序列包含 readiness 核心、FastAPI 接入、Docker smoke 和现状文档四个独立提交。
