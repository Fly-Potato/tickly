# API Container Auto-Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 API 容器默认启动时自动升级数据库到 Alembic head，迁移失败则不启动服务。

**Architecture:** Dockerfile 的默认 `CMD` 调用一个只负责迁移和启动 API 的 POSIX Shell 脚本。脚本先运行 `python -m alembic upgrade head`，成功后用 `exec` 启动服务器；覆盖容器命令时绕过该脚本。本地开发与 FastAPI 生命周期保持不变。

**Tech Stack:** Docker、Docker Compose、POSIX Shell、Alembic 1.18.5、PowerShell

---

## File Map

- Create: `apps/api/docker-entrypoint.sh` — API 容器默认启动顺序及失败传播。
- Create: `.gitattributes` — 强制 Shell 脚本使用 LF，避免 Windows checkout 破坏容器执行。
- Modify: `apps/api/Dockerfile` — 将启动脚本复制为只读文件并设为默认 `CMD`。
- Modify: `scripts/check-compose.ps1` — 对镜像启动脚本、命令顺序和 Dockerfile 接线做仓库级回归检查。
- Modify: `docs/mcp-client-deployment.md` — 删除手动 migration 步骤并说明自动迁移、失败和回滚边界。

### Task 1: Add a failing container-startup contract check

**Files:**
- Modify: `scripts/check-compose.ps1:38-50`
- Test: `scripts/check-compose.ps1`

- [x] **Step 1: Add the startup contract check before image builds**

在依赖顺序检查后加入：

```powershell
# API 默认启动必须先完成 migration；覆盖容器命令时仍可安全执行 CLI 和排障操作。
$apiEntrypointPath = Join-Path $PSScriptRoot "../apps/api/docker-entrypoint.sh"
if (-not (Test-Path -LiteralPath $apiEntrypointPath)) {
    throw "API 镜像缺少自动 migration 启动脚本"
}
$apiEntrypointLines = @(Get-Content -LiteralPath $apiEntrypointPath)
$migrationCommandIndex = [Array]::IndexOf(
    $apiEntrypointLines,
    "python -m alembic upgrade head"
)
$serverCommandIndex = [Array]::IndexOf(
    $apiEntrypointLines,
    "exec python -m app.server"
)
if (
    $migrationCommandIndex -lt 0 -or
    $serverCommandIndex -le $migrationCommandIndex
) {
    throw "API 启动脚本必须先执行 migration，再 exec 启动服务"
}

$apiDockerfile = Get-Content -Raw (Join-Path $PSScriptRoot "../apps/api/Dockerfile")
if (
    $apiDockerfile -notmatch '(?m)^COPY --chmod=0555 --chown=root:root apps/api/docker-entrypoint\.sh /app/docker-entrypoint\.sh$' -or
    $apiDockerfile -notmatch '(?m)^CMD \["/bin/sh", "/app/docker-entrypoint\.sh"\]$'
) {
    throw "API Dockerfile 必须将自动 migration 脚本设为默认命令"
}
```

- [x] **Step 2: Run the check and verify RED**

Run:

```powershell
$env:TICKLY_JWT_SECRET = "j" * 64
$env:TICKLY_MCP_TOKEN_SHA256 = "a" * 64
$env:TICKLY_MCP_ALLOWED_HOSTS = '["localhost:*"]'
$env:TICKLY_MCP_ALLOWED_ORIGINS = '["http://localhost:*"]'
mise exec -- pwsh -NoProfile -File scripts/check-compose.ps1
```

Expected: FAIL with `API 镜像缺少自动 migration 启动脚本` before any image build.

### Task 2: Implement the API container startup path

**Files:**
- Create: `apps/api/docker-entrypoint.sh`
- Create: `.gitattributes`
- Modify: `apps/api/Dockerfile:27-34`
- Test: `scripts/check-compose.ps1`

- [x] **Step 1: Force LF for Shell scripts**

Create `.gitattributes`:

```gitattributes
*.sh text eol=lf
```

- [x] **Step 2: Create the minimal startup script**

Create `apps/api/docker-entrypoint.sh`:

```sh
#!/bin/sh
set -eu

# 容器只在 migration 成功后启动 API；失败退出码直接交给编排系统处理。
python -m alembic upgrade head
exec python -m app.server
```

- [x] **Step 3: Wire the script into the API image**

Add after the Alembic directory copy and replace the current `CMD`:

```dockerfile
COPY --chmod=0555 --chown=root:root apps/api/docker-entrypoint.sh /app/docker-entrypoint.sh

USER tickly

CMD ["/bin/sh", "/app/docker-entrypoint.sh"]
```

- [x] **Step 4: Run the repository checks and verify GREEN**

Run the environment setup from Task 1, then:

```powershell
mise exec -- pwsh -NoProfile -File scripts/check-compose.ps1
mise exec -- pwsh -NoProfile -File scripts/check-compose.ps1 -Traefik
```

Expected: both commands exit 0 and print their existing success messages.

### Task 3: Verify real image migration behavior

**Files:**
- Test: `apps/api/Dockerfile`
- Test: `apps/api/docker-entrypoint.sh`

- [x] **Step 1: Build the API image**

Run:

```powershell
mise exec -- docker build --tag tickly-api:auto-migration-test --file apps/api/Dockerfile .
```

Expected: exit 0 with a runnable non-root API image.

- [x] **Step 2: Verify first start migrates and becomes ready**

Create a uniquely named disposable Docker volume, start the image with production-safe test variables, and poll `/ready` from inside the container:

```powershell
docker volume create tickly-auto-migration-test-data
$jwtSecret = "j" * 64
docker run --detach --name tickly-auto-migration-first `
  --env TICKLY_DATABASE_URL=sqlite:////data/tickly.db `
  --env TICKLY_ENVIRONMENT=production `
  --env TICKLY_HOST=0.0.0.0 `
  --env "TICKLY_JWT_SECRET=$jwtSecret" `
  --env TICKLY_REFRESH_COOKIE_SECURE=true `
  --volume tickly-auto-migration-test-data:/data `
  tickly-api:auto-migration-test
$ready = $false
foreach ($attempt in 1..30) {
    docker exec tickly-auto-migration-first python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8321/ready', timeout=2)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    throw "首次启动后 API 未进入 ready"
}
docker logs tickly-auto-migration-first
```

Expected: `/ready` succeeds and logs include upgrades through `0003_add_cancelled_task_status` before server startup.

- [x] **Step 3: Verify second start skips applied revisions**

Stop and remove the first container, start a second container on the same volume, poll `/ready`, and inspect logs:

```powershell
docker rm --force tickly-auto-migration-first
$jwtSecret = "j" * 64
docker run --detach --name tickly-auto-migration-second `
  --env TICKLY_DATABASE_URL=sqlite:////data/tickly.db `
  --env TICKLY_ENVIRONMENT=production `
  --env TICKLY_HOST=0.0.0.0 `
  --env "TICKLY_JWT_SECRET=$jwtSecret" `
  --env TICKLY_REFRESH_COOKIE_SECURE=true `
  --volume tickly-auto-migration-test-data:/data `
  tickly-api:auto-migration-test
$ready = $false
foreach ($attempt in 1..30) {
    docker exec tickly-auto-migration-second python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8321/ready', timeout=2)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    throw "重复启动后 API 未进入 ready"
}
docker logs tickly-auto-migration-second
```

Expected: `/ready` succeeds and the second-start logs contain no `Running upgrade` entry.

- [x] **Step 4: Verify migration failure prevents server startup**

Run the image against an unwritable SQLite location and capture its output:

```powershell
$jwtSecret = "j" * 64
$failureOutput = docker run --rm `
  --env TICKLY_DATABASE_URL=sqlite:////proc/tickly.db `
  --env TICKLY_ENVIRONMENT=production `
  --env TICKLY_HOST=0.0.0.0 `
  --env "TICKLY_JWT_SECRET=$jwtSecret" `
  --env TICKLY_REFRESH_COOKIE_SECURE=true `
  tickly-api:auto-migration-test 2>&1
$failureExitCode = $LASTEXITCODE
if ($failureExitCode -eq 0 -or $failureOutput -match "Uvicorn running") {
    throw "migration 失败时 API 不得启动"
}
```

Expected: container exits nonzero and output contains no server-start message.

- [x] **Step 5: Clean up only disposable verification resources**

```powershell
docker rm --force tickly-auto-migration-second
docker volume rm tickly-auto-migration-test-data
docker image rm tickly-api:auto-migration-test
```

Expected: only the explicitly named test containers, volume, and image are removed.

### Task 4: Update operations documentation and run final verification

**Files:**
- Modify: `docs/mcp-client-deployment.md:42-55`
- Modify: `docs/mcp-client-deployment.md:87-99`
- Modify: `docs/mcp-client-deployment.md:112-118`

- [x] **Step 1: Document automatic migration**

Change first deployment to pull and start directly, and state that the API container migrates before serving traffic:

````markdown
首次启动或升级时，API 容器会先自动执行 `alembic upgrade head`，成功后才启动服务。迁移失败会使 API 容器退出，并阻止依赖它的 MCP 和 Web 进入可用状态：

```bash
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
docker compose -f compose.yaml -f compose.traefik.yaml ps
```
````

Change the upgrade commands to:

```bash
docker compose -f compose.yaml -f compose.traefik.yaml pull
docker compose -f compose.yaml -f compose.traefik.yaml up --detach
```

Add troubleshooting guidance that `docker compose ... logs api` exposes migration failures and the API must not be force-started against a schema behind head.

- [x] **Step 2: Run scope-appropriate verification**

Run:

```powershell
mise exec -- pnpm test:api
mise exec -- docker compose config --quiet
mise exec -- docker compose -f compose.yaml -f compose.traefik.yaml config --quiet
mise exec -- pwsh -NoProfile -File scripts/check-compose.ps1
mise exec -- pwsh -NoProfile -File scripts/check-compose.ps1 -Traefik
git diff --check
git status --short
```

Expected: API tests pass; both Compose models and both boundary checks exit 0; diff check is clean; only intended implementation files plus the pre-existing `apps/.pytest-release/` remain uncommitted.

- [x] **Step 3: Review and hand off the implementation diff**

Inspect:

```powershell
git diff -- .gitattributes apps/api/docker-entrypoint.sh apps/api/Dockerfile scripts/check-compose.ps1 docs/mcp-client-deployment.md
git status --short
```

Expected: the diff contains only automatic migration behavior, its checks, and deployment documentation. Do not stage or commit implementation files until the user explicitly requests `提交`.
