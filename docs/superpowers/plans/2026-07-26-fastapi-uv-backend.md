# FastAPI + uv Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal, independently managed FastAPI service at `apps/api` with a health endpoint, one test, uv dependency locking, and root monorepo commands.

**Architecture:** `apps/api` is a standalone uv application project rather than a root uv workspace member. mise pins the uv CLI, while uv owns Python 3.13, the local `.venv`, `pyproject.toml`, and `uv.lock`. The pnpm root package only forwards developer commands to the API directory.

**Tech Stack:** mise, uv 0.11.32, Python 3.13, FastAPI with standard extras, pytest, pnpm 11.

## Global Constraints

- Keep `apps/api` independent; do not create a root Python `pyproject.toml` or uv workspace.
- Add `uv = "0.11.32"` to the root `mise.toml`; do not add Python to mise.
- Pin `apps/api/.python-version` to `3.13`.
- Commit `apps/api/uv.lock`; ignore `.venv` and Python caches.
- Add only `fastapi[standard]` as an application dependency and pytest as a development dependency.
- Preserve the existing Web application and keep the root `dev` script targeting `@tickly/web`.
- Do not add a database, ORM, migrations, authentication, CORS, Docker, deployment configuration, custom logging, configuration frameworks, or API version prefixes.

---

### Task 1: Initialize the standalone uv API project

**Files:**
- Modify: `mise.toml`
- Modify: `.gitignore`
- Create: `apps/api/.python-version`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/uv.lock`

**Interfaces:**
- Consumes: the root mise toolchain and the `apps/api` directory boundary from the design.
- Produces: an exact uv 0.11.32 tool, a Python 3.13 application environment, and a reproducible backend lockfile for later tasks.

- [ ] **Step 1: Capture the clean repository and Web baseline**

Run:

```bash
git status --short --branch
mise exec -- pnpm build
mise exec -- pnpm typecheck
```

Expected: the worktree is clean before implementation, and the existing Web build and type check pass.

- [ ] **Step 2: Pin uv in mise and add Python ignore rules**

Update `mise.toml` to:

```toml
[tools]
node = "24"
pnpm = "11"
uv = "0.11.32"
```

Append these entries to `.gitignore`:

```gitignore

# Python
.venv
__pycache__/
.pytest_cache/
*.py[cod]
```

Expected: mise manages the uv binary but does not declare a Python tool.

- [ ] **Step 3: Create the minimal uv project metadata**

Create `apps/api/.python-version`:

```text
3.13
```

Create `apps/api/pyproject.toml`:

```toml
[project]
name = "tickly-api"
version = "0.1.0"
description = "Tickly FastAPI service"
requires-python = ">=3.13"
dependencies = []
```

Expected: the API is an application project with no package build configuration.

- [ ] **Step 4: Install uv and declare the exact dependency groups**

Run:

```bash
mise install uv
mise exec -- uv add --project apps/api "fastapi[standard]"
mise exec -- uv add --project apps/api --dev pytest
```

Expected: uv downloads or selects Python 3.13, creates `apps/api/.venv`, updates `apps/api/pyproject.toml`, and creates `apps/api/uv.lock`. The project dependencies contain `fastapi[standard]`; the dev dependency group contains pytest.

- [ ] **Step 5: Verify locked synchronization**

Run:

```bash
cd apps/api
mise exec -- uv sync --locked
mise exec -- uv run python --version
```

Expected: synchronization succeeds without changing `uv.lock`, and Python reports a `3.13.x` version.

- [ ] **Step 6: Commit the project toolchain**

Run:

```bash
git add mise.toml .gitignore apps/api/.python-version apps/api/pyproject.toml apps/api/uv.lock
git diff --cached --check
git commit -m "chore: initialize uv api project"
```

Expected: the commit contains project metadata and the lockfile, but not `apps/api/.venv` or Python caches.

### Task 2: Implement the health endpoint with TDD

**Files:**
- Create: `apps/api/app/__init__.py`
- Create: `apps/api/app/main.py`
- Create: `apps/api/tests/test_health.py`

**Interfaces:**
- Consumes: `fastapi.FastAPI`, `fastapi.testclient.TestClient`, and the uv environment from Task 1.
- Produces: `app.main.app: FastAPI` and `GET /health -> {"status": "ok"}` with HTTP 200.

- [ ] **Step 1: Write the failing health endpoint test**

Create `apps/api/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the test and verify the red state**

Run:

```bash
cd apps/api
mise exec -- uv run pytest tests/test_health.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app'` because the application module does not exist yet.

- [ ] **Step 3: Add the minimal FastAPI application**

Create an empty `apps/api/app/__init__.py`.

Create `apps/api/app/main.py`:

```python
from fastapi import FastAPI


app = FastAPI(title="Tickly API")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Run the focused test and verify the green state**

Run:

```bash
cd apps/api
mise exec -- uv run pytest tests/test_health.py -v
```

Expected: `1 passed` and `0 failed`.

- [ ] **Step 5: Verify OpenAPI includes the health route**

Run:

```bash
cd apps/api
mise exec -- uv run python -c 'from app.main import app; assert "/health" in app.openapi()["paths"]'
```

Expected: the command exits with status 0 and no output.

- [ ] **Step 6: Commit the API behavior**

Run:

```bash
git add apps/api/app apps/api/tests/test_health.py
git diff --cached --check
git commit -m "feat: add api health endpoint"
```

Expected: the commit contains only the application module and its health test.

### Task 3: Integrate API commands and documentation

**Files:**
- Modify: `package.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: `apps/api/app/main.py`, the API pytest suite, and uv from mise.
- Produces: root `dev:api` and `test:api` commands plus onboarding instructions for both applications.

- [ ] **Step 1: Add root API coordination scripts**

Add these entries to the existing root `scripts` object without changing the current Web scripts:

```json
{
  "dev:api": "cd apps/api && uv run fastapi dev app/main.py",
  "test:api": "cd apps/api && uv run pytest"
}
```

Expected: `dev` still runs `pnpm --filter @tickly/web dev`; pnpm forwards the new commands but does not resolve Python dependencies.

- [ ] **Step 2: Verify the root API test command**

Run:

```bash
mise exec -- pnpm test:api
```

Expected: pytest reports `1 passed` and `0 failed`.

- [ ] **Step 3: Document backend setup and commands**

Update `README.md` so it contains these concepts and commands:

````markdown
Tickly contains:

- `apps/web`: React and Vite frontend
- `apps/api`: FastAPI backend managed by uv

Install all pinned tools:

```bash
mise install
```

Install JavaScript and Python dependencies:

```bash
mise exec -- pnpm install
mise exec -- uv sync --project apps/api --locked
```

Start and test the API:

```bash
mise exec -- pnpm dev:api
mise exec -- pnpm test:api
```
````

Retain the existing Web development, build, lint, typecheck, format, and preview commands.

Expected: a new contributor can prepare and run both applications without manually activating a Python virtual environment.

- [ ] **Step 4: Smoke-test the development server**

From the repository root, start:

```bash
mise exec -- pnpm dev:api
```

In a second command, run:

```bash
curl --fail --silent http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Stop the development server with `Ctrl-C`.

- [ ] **Step 5: Commit monorepo integration**

Run:

```bash
git add package.json README.md
git diff --cached --check
git commit -m "docs: add api development commands"
```

Expected: the commit contains only root command and onboarding changes.

### Task 4: Run final verification

**Files:**
- Verify: `mise.toml`
- Verify: `.gitignore`
- Verify: `package.json`
- Verify: `README.md`
- Verify: `apps/api/.python-version`
- Verify: `apps/api/pyproject.toml`
- Verify: `apps/api/uv.lock`
- Verify: `apps/api/app/main.py`
- Verify: `apps/api/tests/test_health.py`

**Interfaces:**
- Consumes: all deliverables from Tasks 1–3.
- Produces: evidence that the new API and the existing Web application coexist without lockfile or ignored-file regressions.

- [ ] **Step 1: Verify the locked API environment and tests**

Run:

```bash
cd apps/api
mise exec -- uv sync --locked
mise exec -- uv run pytest
```

Expected: lockfile synchronization succeeds and pytest reports `1 passed`, `0 failed`.

- [ ] **Step 2: Verify root coordination**

Run from the repository root:

```bash
mise exec -- pnpm test:api
```

Expected: the root command reports `1 passed`, `0 failed`.

- [ ] **Step 3: Verify the existing Web application**

Run:

```bash
mise exec -- pnpm build
mise exec -- pnpm typecheck
```

Expected: both commands exit successfully and the Web build remains under `apps/web/dist`.

- [ ] **Step 4: Audit versions, locks, ignored files, and Git state**

Run:

```bash
mise exec -- uv --version
cd apps/api
mise exec -- uv run python --version
cd ../..
git status --short --branch
git diff --check
git check-ignore apps/api/.venv apps/api/.pytest_cache
test -f pnpm-lock.yaml
test -f apps/api/uv.lock
```

Expected:

- uv reports `0.11.32`.
- Python reports `3.13.x`.
- `main` contains only the intended new commits and no uncommitted source changes.
- `.venv` and `.pytest_cache` are ignored.
- both JavaScript and Python lockfiles exist.

## Self-review checklist

- [ ] Every requirement in the approved design has a corresponding implementation or verification step.
- [ ] The only runtime route is `GET /health`.
- [ ] mise owns uv but not Python; uv owns Python 3.13 and project dependencies.
- [ ] No root uv workspace or unrelated backend infrastructure is introduced.
- [ ] The failing test is observed before the application module is created.
- [ ] Existing Web build and typecheck commands remain part of final verification.
