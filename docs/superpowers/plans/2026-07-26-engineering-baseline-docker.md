# Tickly Engineering Baseline and Docker Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Web and API baseline green, modularize FastAPI infrastructure, and add production-shaped Web/API containers with an automated Compose smoke test.

**Architecture:** Keep native mise/pnpm/uv commands as the daily development path. Build a moderately modular FastAPI app with explicit settings, lifespan, infrastructure routes, request IDs, structured logging, and uniform errors; package the React build behind Caddy and proxy infrastructure/API paths to a single non-root FastAPI container.

**Tech Stack:** React 19, TypeScript 6, Vite 8, pnpm 11.16.0, Python 3.13.14, FastAPI, pydantic-settings, pytest, uv 0.11.32, Node.js 24.18.0, Caddy 2.11.4, Docker Compose

## Global Constraints

- Do not implement SQLAlchemy, SQLite, Alembic, authentication, Todo behavior, AI behavior, HTTPS, CI/CD, or Docker hot reload.
- Native development remains `mise exec -- pnpm dev` and `mise exec -- pnpm dev:api`.
- Docker is only for production-shaped build and smoke verification.
- Keep `GET /health` lightweight and unversioned; add unversioned `GET /ready`.
- Future business routes aggregate below `/api/v1`; do not add a fake business endpoint.
- Use `pydantic-settings` with the `TICKLY_` prefix and explicit Settings injection in tests.
- Keep Vite proxy paths unchanged and keep API URLs relative in browser code.
- Keep the existing fast-refresh ESLint rule; fix the component export boundary instead of suppressing the rule.
- Use only standard-library logging; write logs to stdout.
- Do not use floating `latest` container tags.
- Run containers as non-root users.
- The API service must not publish port 8000 to the host.
- All work happens on the current branch only with the user's explicit authorization or in an isolated worktree.

---

### Task 1: Restore the Web baseline and add the native development proxy

**Files:**

- Create: `apps/web/src/components/ui/button-variants.ts`
- Modify: `apps/web/src/components/ui/button.tsx`
- Modify: `apps/web/vite.config.ts`

**Interfaces:**

- Consumes: Existing `Button`, `buttonVariants`, `@/` alias, and Vite 8 config.
- Produces: `Button` from `@/components/ui/button`, `buttonVariants` from `@/components/ui/button-variants`, and a development-only `/api` proxy controlled by `VITE_API_PROXY_TARGET`.

- [ ] **Step 1: Reproduce the existing lint failure**

Run:

```bash
env CI=true mise exec -- pnpm lint
```

Expected: FAIL only because `apps/web/src/components/ui/button.tsx` exports both a component and `buttonVariants`, triggering `react-refresh/only-export-components`.

- [ ] **Step 2: Split the variant definition from the React component**

Create `apps/web/src/components/ui/button-variants.ts`:

```ts
import { cva } from "class-variance-authority"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/80",
        outline:
          "border-border bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)] aria-expanded:bg-secondary aria-expanded:text-secondary-foreground",
        ghost:
          "hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:hover:bg-muted/50",
        destructive:
          "bg-destructive/10 text-destructive hover:bg-destructive/20 focus-visible:border-destructive/40 focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:hover:bg-destructive/30 dark:focus-visible:ring-destructive/40",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default:
          "h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        xs: "h-6 gap-1 rounded-[min(var(--radius-md),10px)] px-2 text-xs in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 rounded-[min(var(--radius-md),12px)] px-2.5 text-[0.8rem] in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        icon: "size-8",
        "icon-xs":
          "size-6 rounded-[min(var(--radius-md),10px)] in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-7 rounded-[min(var(--radius-md),12px)] in-data-[slot=button-group]:rounded-lg",
        "icon-lg": "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export { buttonVariants }
```

Replace `apps/web/src/components/ui/button.tsx` with:

```tsx
import { Button as ButtonPrimitive } from "@base-ui/react/button"
import type { VariantProps } from "class-variance-authority"

import { buttonVariants } from "@/components/ui/button-variants"
import { cn } from "@/lib/utils"

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button }
```

- [ ] **Step 3: Add the Vite development proxy**

Replace `apps/web/vite.config.ts` with:

```ts
import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const apiProxyTarget =
    env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000"

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
```

- [ ] **Step 4: Verify the Web baseline**

Run:

```bash
env CI=true mise exec -- pnpm lint
env CI=true mise exec -- pnpm typecheck
env CI=true mise exec -- pnpm build
```

Expected: all three commands exit 0 with no lint or type errors.

- [ ] **Step 5: Commit the Web baseline**

```bash
git add apps/web/src/components/ui/button-variants.ts apps/web/src/components/ui/button.tsx apps/web/vite.config.ts
git commit -m "fix(web): restore development baseline"
```

---

### Task 2: Add typed API settings

**Files:**

- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Create: `apps/api/.env.example`
- Create: `apps/api/app/core/__init__.py`
- Create: `apps/api/app/core/config.py`
- Create: `apps/api/tests/test_config.py`

**Interfaces:**

- Consumes: Environment variables prefixed with `TICKLY_` and optional `apps/api/.env`.
- Produces: `Environment`, `LogLevel`, and `Settings`; later tasks call `Settings(_env_file=None)` in tests and `Settings()` in production.

- [ ] **Step 1: Add the runtime dependency**

Run:

```bash
env UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv add --project apps/api pydantic-settings
```

Expected: `apps/api/pyproject.toml` and `apps/api/uv.lock` change, and uv exits 0.

- [ ] **Step 2: Write failing settings tests**

Create `apps/api/tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings


def test_default_settings_are_for_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.app_name == "Tickly API"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.log_level == "INFO"
    assert settings.log_json is False
    assert settings.request_id_header == "X-Request-ID"


def test_tickly_prefixed_environment_variables_are_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TICKLY_ENVIRONMENT", "test")
    monkeypatch.setenv("TICKLY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TICKLY_LOG_JSON", "true")

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.TEST
    assert settings.log_level == "DEBUG"
    assert settings.log_json is True


@pytest.mark.parametrize("value", ["staging", "local", "prod"])
def test_invalid_environment_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment=value, _env_file=None)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="TRACE", _env_file=None)


@pytest.mark.parametrize("value", ["api/v1", "/api/v1/", "/"])
def test_invalid_api_prefix_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_v1_prefix=value, _env_file=None)
```

- [ ] **Step 3: Run the settings tests to verify they fail**

Run:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/tickly-uv-cache ./.venv/bin/pytest tests/test_config.py -v
```

Expected: FAIL during collection because `app.core.config` does not exist.

- [ ] **Step 4: Implement Settings**

Create an empty `apps/api/app/core/__init__.py`.

Create `apps/api/app/core/config.py`:

```python
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


API_ROOT = Path(__file__).resolve().parents[2]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TICKLY_",
        env_file=API_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "Tickly API"
    api_v1_prefix: str = "/api/v1"
    log_level: LogLevel = "INFO"
    log_json: bool = False
    request_id_header: str = "X-Request-ID"

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        if value == "/" or not value.startswith("/") or value.endswith("/"):
            raise ValueError(
                "api_v1_prefix must start with '/' and must not end with '/'"
            )
        return value
```

Create `apps/api/.env.example`:

```dotenv
TICKLY_ENVIRONMENT=development
TICKLY_LOG_LEVEL=INFO
TICKLY_LOG_JSON=false
TICKLY_REQUEST_ID_HEADER=X-Request-ID
```

- [ ] **Step 5: Verify Settings**

Run:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/tickly-uv-cache ./.venv/bin/pytest tests/test_config.py -v
```

Expected: all settings tests pass.

- [ ] **Step 6: Commit typed settings**

```bash
git add apps/api/.env.example apps/api/pyproject.toml apps/api/uv.lock apps/api/app/core apps/api/tests/test_config.py
git commit -m "feat(api): add typed settings"
```

---

### Task 3: Introduce the FastAPI application factory and infrastructure routes

**Files:**

- Create: `apps/api/app/api/__init__.py`
- Create: `apps/api/app/api/router.py`
- Create: `apps/api/app/api/routes/__init__.py`
- Create: `apps/api/app/api/routes/health.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/test_health.py`

**Interfaces:**

- Consumes: `Settings` from Task 2.
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`, module-level `app`, unversioned `GET /health`, unversioned `GET /ready`, and empty `api_router` for future `/api/v1` routes.

- [ ] **Step 1: Replace the health test with factory and lifespan expectations**

Replace `apps/api/tests/test_health.py` with:

```python
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


def make_app():
    return create_app(
        Settings(environment=Environment.TEST, _env_file=None)
    )


def test_health_does_not_require_lifespan_readiness() -> None:
    app = make_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    client.close()


def test_ready_requires_lifespan() -> None:
    app = make_app()
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 503
    client.close()


def test_lifespan_marks_the_app_ready_and_cleans_up() -> None:
    app = make_app()
    assert app.state.ready is False

    with TestClient(app) as client:
        assert app.state.ready is True
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    assert app.state.ready is False
```

- [ ] **Step 2: Run the health tests to verify they fail**

Run:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/tickly-uv-cache ./.venv/bin/pytest tests/test_health.py -v
```

Expected: FAIL because `create_app` and `/ready` do not exist.

- [ ] **Step 3: Create the router modules**

Create empty files:

```text
apps/api/app/api/__init__.py
apps/api/app/api/routes/__init__.py
```

Create `apps/api/app/api/router.py`:

```python
from fastapi import APIRouter


api_router = APIRouter()
```

Create `apps/api/app/api/routes/health.py`:

```python
from fastapi import APIRouter, HTTPException, Request, status


router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        )
    return {"status": "ready"}
```

- [ ] **Step 4: Implement the application factory**

Replace `apps/api/app/main.py` with:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.config import Settings


logger = logging.getLogger("tickly.lifecycle")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ready = True
    logger.info("application.started")
    try:
        yield
    finally:
        app.state.ready = False
        logger.info("application.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.ready = False
    application.include_router(health_router)
    application.include_router(
        api_router,
        prefix=resolved_settings.api_v1_prefix,
    )
    return application


app = create_app()
```

- [ ] **Step 5: Verify the application factory and health routes**

Run:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/tickly-uv-cache ./.venv/bin/pytest tests/test_config.py tests/test_health.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit the application factory**

```bash
git add apps/api/app/api apps/api/app/main.py apps/api/tests/test_health.py
git commit -m "refactor(api): add application factory"
```

---

### Task 4: Add request IDs, uniform errors, and structured logging

**Files:**

- Create: `apps/api/app/core/errors.py`
- Create: `apps/api/app/core/logging.py`
- Create: `apps/api/app/middleware/__init__.py`
- Create: `apps/api/app/middleware/request_id.py`
- Modify: `apps/api/app/api/routes/health.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/test_health.py`
- Create: `apps/api/tests/test_errors.py`
- Create: `apps/api/tests/test_logging.py`
- Create: `apps/api/tests/test_request_id.py`

**Interfaces:**

- Consumes: `Settings`, `create_app`, `/health`, and `/ready`.
- Produces: `AppError`, `register_exception_handlers(app)`, `JsonFormatter`, `configure_logging(settings)`, and `RequestIdMiddleware`; every HTTP response returns the configured request ID header.

- [ ] **Step 1: Write request ID tests**

Create `apps/api/tests/test_request_id.py`:

```python
import re

from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def make_client() -> TestClient:
    app = create_app(
        Settings(environment=Environment.TEST, _env_file=None)
    )
    return TestClient(app)


def test_missing_request_id_is_generated() -> None:
    with make_client() as client:
        response = client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert REQUEST_ID_PATTERN.fullmatch(request_id)


def test_valid_request_id_is_preserved() -> None:
    with make_client() as client:
        response = client.get(
            "/health",
            headers={"X-Request-ID": "web.request-123"},
        )

    assert response.headers["X-Request-ID"] == "web.request-123"


def test_invalid_request_id_is_replaced() -> None:
    with make_client() as client:
        response = client.get(
            "/health",
            headers={"X-Request-ID": "contains a space"},
        )

    assert response.headers["X-Request-ID"] != "contains a space"
    assert REQUEST_ID_PATTERN.fullmatch(response.headers["X-Request-ID"])


def test_overlong_request_id_is_replaced() -> None:
    supplied = "x" * 129
    with make_client() as client:
        response = client.get(
            "/health",
            headers={"X-Request-ID": supplied},
        )

    assert response.headers["X-Request-ID"] != supplied
    assert len(response.headers["X-Request-ID"]) <= 128
```

- [ ] **Step 2: Write uniform error tests**

Create `apps/api/tests/test_errors.py`:

```python
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


def make_app():
    app = create_app(
        Settings(environment=Environment.TEST, _env_file=None)
    )

    @app.get("/test/validate/{item_id}")
    async def validate_item_id(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/test/http-error")
    async def raise_http_error() -> None:
        raise HTTPException(status_code=418, detail="Short and stout")

    @app.get("/test/boom")
    async def raise_unhandled_error() -> None:
        raise RuntimeError("secret internal text")

    return app


def test_unknown_route_uses_uniform_error() -> None:
    with TestClient(make_app()) as client:
        response = client.get(
            "/missing",
            headers={"X-Request-ID": "missing-route"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "资源不存在",
            "request_id": "missing-route",
            "details": [],
        }
    }


def test_validation_error_uses_uniform_error() -> None:
    with TestClient(make_app()) as client:
        response = client.get(
            "/test/validate/not-an-integer",
            headers={"X-Request-ID": "validation"},
        )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "请求参数无效"
    assert body["error"]["request_id"] == "validation"
    assert body["error"]["details"][0]["location"] == ["path", "item_id"]
    assert "input" not in body["error"]["details"][0]


def test_explicit_http_error_preserves_status() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/test/http-error")

    assert response.status_code == 418
    assert response.json()["error"]["code"] == "http_error"
    assert response.json()["error"]["message"] == "Short and stout"


def test_unhandled_error_does_not_leak_exception_text() -> None:
    with TestClient(
        make_app(),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/test/boom",
            headers={"X-Request-ID": "boom"},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "boom"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "服务器内部错误",
            "request_id": "boom",
            "details": [],
        }
    }
    assert "secret internal text" not in response.text
```

- [ ] **Step 3: Write structured logging tests**

Create `apps/api/tests/test_logging.py`:

```python
import json
import logging

from app.core.logging import JsonFormatter


def test_json_formatter_emits_access_fields() -> None:
    record = logging.LogRecord(
        name="tickly.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="request.completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "json-log"
    record.method = "GET"
    record.path = "/health"
    record.status = 200
    record.duration_ms = 1.25

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["message"] == "request.completed"
    assert payload["request_id"] == "json-log"
    assert payload["method"] == "GET"
    assert payload["path"] == "/health"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.25
    assert payload["timestamp"].endswith("+00:00")
```

- [ ] **Step 4: Add the final readiness error expectation**

Append this test to `apps/api/tests/test_health.py`:

```python
def test_not_ready_uses_uniform_error() -> None:
    app = make_app()
    client = TestClient(app)

    response = client.get(
        "/ready",
        headers={"X-Request-ID": "not-ready"},
    )

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
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/tickly-uv-cache ./.venv/bin/pytest tests/test_request_id.py tests/test_errors.py tests/test_logging.py tests/test_health.py -v
```

Expected: FAIL because the middleware, formatter, and exception handlers do not exist.

- [ ] **Step 6: Implement structured logging**

Create `apps/api/app/core/logging.py`:

```python
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any

from app.core.config import Settings


ACCESS_FIELDS = (
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ACCESS_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)
```

- [ ] **Step 7: Implement request ID middleware**

Create an empty `apps/api/app/middleware/__init__.py`.

Create `apps/api/app/middleware/request_id.py`:

```python
import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
access_logger = logging.getLogger("tickly.access")


class RequestIdMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        header_name: str = "X-Request-ID",
    ) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get(self.header_name)
        request_id = (
            supplied
            if supplied and REQUEST_ID_PATTERN.fullmatch(supplied)
            else str(uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        response_status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                headers = MutableHeaders(scope=message)
                headers[self.header_name] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            access_logger.info(
                "request.completed",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": response_status,
                    "duration_ms": round(
                        (perf_counter() - started) * 1000,
                        3,
                    ),
                },
            )
```

- [ ] **Step 8: Implement uniform errors**

Create `apps/api/app/core/errors.py`:

```python
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger("tickly.errors")


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def request_id_from(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_content(
    request: Request,
    *,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_from(request),
            "details": details or [],
        }
    }


def response_headers(
    request: Request,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = dict(existing or {})
    header_name = request.app.state.settings.request_id_header
    headers[header_name] = request_id_from(request)
    return headers


async def handle_app_error(
    request: Request,
    exc: AppError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_content(
            request,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ),
        headers=response_headers(request),
    )


async def handle_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = [
        {
            "location": list(error["loc"]),
            "type": error["type"],
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_content(
            request,
            code="validation_error",
            message="请求参数无效",
            details=details,
        ),
        headers=response_headers(request),
    )


async def handle_http_error(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    if exc.status_code == 404:
        code = "not_found"
        message = "资源不存在"
    else:
        code = "http_error"
        message = (
            exc.detail
            if isinstance(exc.detail, str)
            else "请求处理失败"
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_content(
            request,
            code=code,
            message=message,
        ),
        headers=response_headers(request, exc.headers),
    )


async def handle_unexpected_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception(
        "request.failed",
        extra={"request_id": request_id_from(request)},
    )
    return JSONResponse(
        status_code=500,
        content=error_content(
            request,
            code="internal_error",
            message="服务器内部错误",
        ),
        headers=response_headers(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(
        RequestValidationError,
        handle_validation_error,
    )
    app.add_exception_handler(
        StarletteHTTPException,
        handle_http_error,
    )
    app.add_exception_handler(Exception, handle_unexpected_error)
```

- [ ] **Step 9: Use `AppError` for readiness**

Replace `apps/api/app/api/routes/health.py` with:

```python
from fastapi import APIRouter, Request, status

from app.core.errors import AppError


router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="not_ready",
            message="服务尚未就绪",
        )
    return {"status": "ready"}
```

- [ ] **Step 10: Register logging, middleware, and handlers**

Update `apps/api/app/main.py` imports:

```python
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware.request_id import RequestIdMiddleware
```

Inside `create_app()`, immediately after resolving settings, configure logging:

```python
    configure_logging(resolved_settings)
```

After creating the FastAPI instance and before including routers, add:

```python
    application.add_middleware(
        RequestIdMiddleware,
        header_name=resolved_settings.request_id_header,
    )
    register_exception_handlers(application)
```

- [ ] **Step 11: Verify the complete API infrastructure**

Run:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/tickly-uv-cache ./.venv/bin/pytest -v
```

Expected: all API tests pass with no failures.

- [ ] **Step 12: Commit API infrastructure**

```bash
git add apps/api/app apps/api/tests
git commit -m "feat(api): add HTTP infrastructure"
```

---

### Task 5: Build the non-root API image

**Files:**

- Create: `.dockerignore`
- Create: `apps/api/Dockerfile`

**Interfaces:**

- Consumes: `apps/api/pyproject.toml`, `apps/api/uv.lock`, `apps/api/app`, Python 3.13.14, and uv 0.11.32.
- Produces: `tickly-api:stage0`, listening on container port 8000 as UID 10001 with production dependencies only.

- [ ] **Step 1: Create the root Docker ignore file**

Create `.dockerignore`:

```dockerignore
.git
.github
.agents
.codex
.worktrees
node_modules
**/node_modules
.pnpm-store
**/.pnpm-store
.venv
**/.venv
dist
**/dist
__pycache__
**/__pycache__
.pytest_cache
**/.pytest_cache
*.py[cod]
*.log
.env
**/.env
**/.env.*
.DS_Store
.idea
.vscode
docs
**/tests
```

- [ ] **Step 2: Create the API Dockerfile**

Create `apps/api/Dockerfile`:

```dockerfile
FROM python:3.13.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-cache


FROM python:3.13.14-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 tickly \
    && useradd --system --uid 10001 --gid tickly \
        --home-dir /app --shell /usr/sbin/nologin tickly

WORKDIR /app

COPY --from=builder --chown=tickly:tickly /app/.venv /app/.venv
COPY --chown=tickly:tickly apps/api/app /app/app

USER tickly

EXPOSE 8000

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build and inspect the API image**

Run:

```bash
docker build --file apps/api/Dockerfile --tag tickly-api:stage0 .
docker run --rm tickly-api:stage0 id -u
docker run --rm tickly-api:stage0 sh -c 'test ! -d /app/tests && test ! -x /app/.venv/bin/pytest'
```

Expected:

- Build exits 0.
- `id -u` prints `10001`.
- The runtime image contains neither `/app/tests` nor a pytest executable.

- [ ] **Step 4: Run an API container smoke check**

Run:

```bash
api_container=$(docker run --detach --publish 18000:8000 tickly-api:stage0)
trap 'docker rm --force "$api_container" >/dev/null 2>&1 || true' EXIT
for attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18000/health >/tmp/tickly-api-health.json; then
    break
  fi
  sleep 1
done
test "$(cat /tmp/tickly-api-health.json)" = '{"status":"ok"}'
docker rm --force "$api_container"
trap - EXIT
```

Expected: health returns exactly `{"status":"ok"}` within 30 seconds, and the container is removed.

- [ ] **Step 5: Commit the API image**

```bash
git add .dockerignore apps/api/Dockerfile
git commit -m "build(api): add production image"
```

---

### Task 6: Build the non-root Caddy Web image

**Files:**

- Create: `apps/web/Caddyfile`
- Create: `apps/web/Dockerfile`

**Interfaces:**

- Consumes: Root pnpm workspace, `@tickly/web`, Node.js 24.18.0, pnpm 11.16.0, and Caddy 2.11.4.
- Produces: `tickly-web:stage0`, serving the React SPA on container port 8080 as the `caddy` user and proxying `/api/*`, `/health`, and `/ready` to `api:8000`.

- [ ] **Step 1: Create the Caddy route configuration**

Create `apps/web/Caddyfile`:

```caddyfile
:8080 {
	encode zstd gzip

	@api path /api/* /health /ready
	handle @api {
		reverse_proxy api:8000
	}

	handle {
		root * /srv
		route {
			try_files {path} /index.html
			header /index.html Cache-Control "public, max-age=0, must-revalidate"
			file_server
		}
	}
}
```

- [ ] **Step 2: Create the Web Dockerfile**

Create `apps/web/Dockerfile`:

```dockerfile
FROM node:24.18.0-bookworm-slim AS builder

RUN corepack enable \
    && corepack prepare pnpm@11.16.0 --activate

WORKDIR /workspace

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @tickly/web...

COPY apps/web apps/web
RUN pnpm --filter @tickly/web build


FROM caddy:2.11.4-alpine AS runtime

COPY apps/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=builder --chown=caddy:caddy /workspace/apps/web/dist /srv

RUN chown -R caddy:caddy /config /data /srv

USER caddy

EXPOSE 8080
```

- [ ] **Step 3: Build and inspect the Web image**

Run:

```bash
docker build --file apps/web/Dockerfile --tag tickly-web:stage0 .
test "$(
  docker image inspect \
    --format '{{.Config.User}}' \
    tickly-web:stage0
)" = "caddy"
```

Expected:

- Build exits 0.
- The image config uses the `caddy` user.

- [ ] **Step 4: Verify static SPA serving**

Run:

```bash
web_container=$(docker run --detach --publish 18080:8080 tickly-web:stage0)
trap 'docker rm --force "$web_container" >/dev/null 2>&1 || true' EXIT
for attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18080/ >/tmp/tickly-web-index.html; then
    break
  fi
  sleep 1
done
rg -q 'id="root"' /tmp/tickly-web-index.html
curl --fail --silent http://127.0.0.1:18080/client-side-route | rg -q 'id="root"'
docker rm --force "$web_container"
trap - EXIT
```

Expected: both `/` and a nonexistent client-side route return the SPA document, and the container is removed.

- [ ] **Step 5: Commit the Web image**

```bash
git add apps/web/Caddyfile apps/web/Dockerfile
git commit -m "build(web): add Caddy image"
```

---

### Task 7: Compose the services and automate the container smoke test

**Files:**

- Create: `compose.yaml`
- Create: `scripts/docker-smoke.sh`
- Modify: `package.json`

**Interfaces:**

- Consumes: API and Web Dockerfiles from Tasks 5 and 6.
- Produces: `api` and `web` Compose services plus root `docker:build`, `docker:up`, `docker:down`, and `docker:smoke` commands.

- [ ] **Step 1: Create the Compose file**

Create `compose.yaml`:

```yaml
services:
  api:
    build:
      context: .
      dockerfile: apps/api/Dockerfile
    environment:
      TICKLY_ENVIRONMENT: production
      TICKLY_LOG_JSON: "true"
    expose:
      - "8000"
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import urllib.request;
          urllib.request.urlopen(
            "http://127.0.0.1:8000/health",
            timeout=2
          )
      interval: 2s
      timeout: 3s
      retries: 15
      start_period: 5s

  web:
    build:
      context: .
      dockerfile: apps/web/Dockerfile
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "8080:8080"
    healthcheck:
      test:
        - CMD
        - wget
        - --quiet
        - --tries=1
        - --spider
        - http://127.0.0.1:8080/
      interval: 2s
      timeout: 3s
      retries: 15
      start_period: 5s
```

- [ ] **Step 2: Create the smoke script**

Create `scripts/docker-smoke.sh`:

```sh
#!/bin/sh
set -eu

cleanup() {
  docker compose down --remove-orphans >/dev/null 2>&1 || true
}

fail() {
  printf '%s\n' "$1" >&2
  docker compose ps >&2 || true
  docker compose logs >&2 || true
  exit 1
}

wait_for_healthy() {
  service="$1"
  remaining=60

  while [ "$remaining" -gt 0 ]; do
    container_id="$(docker compose ps --quiet "$service")"
    if [ -n "$container_id" ]; then
      health="$(
        docker inspect \
          --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
          "$container_id"
      )"
      if [ "$health" = "healthy" ]; then
        return 0
      fi
      if [ "$health" = "unhealthy" ]; then
        fail "$service became unhealthy"
      fi
    fi
    sleep 1
    remaining=$((remaining - 1))
  done

  fail "$service did not become healthy within 60 seconds"
}

trap cleanup EXIT INT TERM

docker compose config --quiet
docker compose build
docker compose up --detach

wait_for_healthy api
wait_for_healthy web

curl --fail --silent http://127.0.0.1:8080/ \
  | grep -q 'id="root"'

test "$(
  curl --fail --silent http://127.0.0.1:8080/health
)" = '{"status":"ok"}'

test "$(
  curl --fail --silent http://127.0.0.1:8080/ready
)" = '{"status":"ready"}'

api_container="$(docker compose ps --quiet api)"
api_binding="$(
  docker inspect \
    --format '{{with index .NetworkSettings.Ports "8000/tcp"}}{{json .}}{{end}}' \
    "$api_container"
)"
test -z "$api_binding" || fail "api port 8000 is published to the host"

test "$(docker compose exec --no-TTY api id -u)" != "0" \
  || fail "api runs as root"
test "$(docker compose exec --no-TTY web id -u)" != "0" \
  || fail "web runs as root"

printf '%s\n' "Docker smoke test passed"
```

Make it executable:

```bash
chmod +x scripts/docker-smoke.sh
```

- [ ] **Step 3: Add root orchestration scripts**

Update the root `package.json` scripts object to:

```json
{
  "scripts": {
    "dev": "pnpm --filter @tickly/web dev",
    "build": "pnpm --filter @tickly/web build",
    "lint": "pnpm --filter @tickly/web lint",
    "format": "pnpm --filter @tickly/web format",
    "typecheck": "pnpm --filter @tickly/web typecheck",
    "preview": "pnpm --filter @tickly/web preview",
    "dev:api": "cd apps/api && uv run fastapi dev app/main.py",
    "test:api": "cd apps/api && uv run pytest",
    "check": "pnpm lint && pnpm typecheck && pnpm build && pnpm test:api",
    "docker:build": "docker compose build",
    "docker:up": "docker compose up --detach --build",
    "docker:down": "docker compose down --remove-orphans",
    "docker:smoke": "./scripts/docker-smoke.sh"
  }
}
```

- [ ] **Step 4: Validate Compose without starting services**

Run:

```bash
docker compose config --quiet
```

Expected: exit 0 with no validation errors.

- [ ] **Step 5: Run the full container smoke test**

Run:

```bash
mise exec -- pnpm docker:smoke
```

Expected:

- Both images build.
- Both services become healthy.
- Web, `/health`, and `/ready` checks pass through port 8080.
- API port 8000 is not published.
- Both processes are non-root.
- Output ends with `Docker smoke test passed`.
- `docker compose ps --quiet` returns no container IDs after the script exits.

- [ ] **Step 6: Commit Compose automation**

```bash
git add compose.yaml scripts/docker-smoke.sh package.json
git commit -m "build: add Compose smoke test"
```

---

### Task 8: Document and verify the completed engineering baseline

**Files:**

- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**

- Consumes: Native and Docker commands from Tasks 1–7.
- Produces: Accurate contributor instructions for native development, aggregate checks, and production-shaped Docker smoke verification.

- [ ] **Step 1: Replace the README with the final stage-0 instructions**

Replace `README.md` with:

````markdown
# Tickly

Tickly is a pnpm monorepo for a personal multi-device Todo application with planned AI-assisted task creation.

- `apps/web`: React and Vite frontend
- `apps/api`: FastAPI backend managed by uv
- `packages/*`: reusable workspace packages
- `docs/roadmaps`: cross-phase product and engineering roadmaps
- `docs/superpowers`: approved phase designs and implementation plans

The Todo domain, authentication, database, and AI behavior are not implemented yet.

## Setup

Node.js, pnpm, and uv are pinned by the root `mise.toml`. uv manages the API's Python 3.13 environment and dependencies.

Install all pinned tools and dependencies:

```bash
mise install
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
```

## Native development

Run the Web application:

```bash
mise exec -- pnpm dev
```

Run the API in another terminal:

```bash
mise exec -- pnpm dev:api
```

Vite proxies `/api` to `http://127.0.0.1:8000` by default. Set `VITE_API_PROXY_TARGET` for a different local target.

The API currently exposes:

- `GET /health`
- `GET /ready`
- `/docs`
- `/redoc`
- `/openapi.json`

## Checks

Run every native check:

```bash
mise exec -- pnpm check
```

Individual checks:

```bash
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:api
```

Formatting modifies TypeScript and TSX files:

```bash
mise exec -- pnpm format
```

## Docker verification

Daily development runs natively. Docker verifies production-shaped images and is not configured for source hot reload.

Build and start the stack:

```bash
mise exec -- pnpm docker:up
```

Open `http://localhost:8080`.

Stop the stack:

```bash
mise exec -- pnpm docker:down
```

Run the complete, self-cleaning container smoke test:

```bash
mise exec -- pnpm docker:smoke
```

The Web container is the only public entry point. It serves the React build and proxies `/api/*`, `/health`, and `/ready` to the API container. The API does not publish port 8000 to the host.

## Dependency locks

- JavaScript dependencies: root `pnpm-lock.yaml`
- Python API dependencies: `apps/api/uv.lock`
````

- [ ] **Step 2: Extend the repository verification rules**

Under `## 验证要求` in `AGENTS.md`, add:

```markdown
- Dockerfile、Caddyfile、`.dockerignore`、Compose 或容器脚本改动：运行 `mise exec -- pnpm docker:smoke`。
- API 结构、配置、middleware 或错误处理改动：运行 `mise exec -- pnpm test:api`。
```

- [ ] **Step 3: Run fresh native verification**

Run:

```bash
env CI=true mise exec -- pnpm install --frozen-lockfile
env UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv sync --project apps/api --locked
env CI=true UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- pnpm check
```

Expected:

- Frozen JavaScript install succeeds.
- Locked Python sync succeeds.
- Web lint, typecheck, and build pass.
- Every API test passes.

- [ ] **Step 4: Run fresh container verification**

Run:

```bash
mise exec -- pnpm docker:smoke
docker compose ps --quiet
```

Expected:

- Smoke output ends with `Docker smoke test passed`.
- The second command prints nothing because cleanup removed the containers.

- [ ] **Step 5: Verify scope and generated files**

Run:

```bash
git diff --check
git status --short
test -z "$(
  git ls-files \
    | rg '(^|/)\.env$|\.pyc$' \
    || true
)"
```

Expected:

- `git diff --check` emits no errors.
- Git status contains only files named in this plan.
- No tracked `.env` or `.pyc` file exists.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md AGENTS.md
git commit -m "docs: document engineering baseline"
```

- [ ] **Step 7: Record final evidence**

Run:

```bash
git status --short
git log -8 --oneline
```

Expected:

- The worktree is clean.
- Eight implementation commits from Tasks 1–8 appear after the plan commit.
