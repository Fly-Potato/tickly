$ErrorActionPreference = "Stop"

# 解析 Compose 的最终模型，避免只靠文本匹配漏掉环境变量插值或默认值问题。
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

# API 与 MCP 只能通过 Compose 内部网络访问，宿主机入口由 Web/Caddy 独占。
if ($null -ne $config.services.api.ports -or $null -ne $config.services.mcp.ports) {
    throw "API 和 MCP 不得发布宿主机端口"
}
if ($null -eq $config.services.web.ports) {
    throw "Web 必须是唯一发布宿主机端口的服务"
}

# 两个服务必须校验同一原始 Token 的哈希，防止内部转发出现认证配置漂移。
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

# 构建上下文必须排除 API 运行期数据库，避免把本地任务数据带进镜像层。
$dockerIgnoreLines = @(Get-Content (Join-Path $PSScriptRoot "../.dockerignore"))
foreach ($databaseRule in @(
    "apps/api/data/*.db",
    "apps/api/data/*.db-shm",
    "apps/api/data/*.db-wal",
    "apps/api/data/*.db-journal"
)) {
    if ($databaseRule -notin $dockerIgnoreLines) {
        throw ".dockerignore 缺少数据库排除规则：$databaseRule"
    }
}

# 应用与依赖在镜像构建期由 root 写入，运行账号只能读取和执行。
$mcpDockerfile = Get-Content -Raw (Join-Path $PSScriptRoot "../apps/mcp/Dockerfile")
if ($mcpDockerfile -match '(?m)^COPY .*--chown=tickly-mcp:tickly-mcp') {
    throw "MCP runtime 文件不得归运行账号所有"
}

# 由刚构建的目标 Web 镜像实际解析 Caddyfile；注释和失效 matcher 不会进入语义模型。
docker compose build --quiet web | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Web 镜像构建失败"
}
$webImage = "$($config.name)-web"
$apiPort = $config.services.web.environment.TICKLY_API_PORT
$mcpPort = $config.services.web.environment.TICKLY_MCP_PORT
docker run --rm --network none --entrypoint caddy `
    --env "TICKLY_API_PORT=$apiPort" --env "TICKLY_MCP_PORT=$mcpPort" `
    $webImage validate --config /etc/caddy/Caddyfile --adapter caddyfile | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Caddy validate 执行失败"
}
$rawCaddyConfig = docker run --rm --network none --entrypoint caddy `
    --env "TICKLY_API_PORT=$apiPort" --env "TICKLY_MCP_PORT=$mcpPort" `
    $webImage adapt --config /etc/caddy/Caddyfile --adapter caddyfile
if ($LASTEXITCODE -ne 0) {
    throw "Caddy adapt 执行失败"
}
$caddyConfig = $rawCaddyConfig | ConvertFrom-Json
$caddyServers = @($caddyConfig.apps.http.servers.PSObject.Properties.Value)
$caddyRoutes = @($caddyServers | ForEach-Object { @($_.routes) })
$indexedRoutes = @(
    for ($routeIndex = 0; $routeIndex -lt $caddyRoutes.Count; $routeIndex++) {
        $route = $caddyRoutes[$routeIndex]
        [pscustomobject]@{
            Index = $routeIndex
            Route = $route
            Paths = @($route.match | ForEach-Object { @($_.path) })
        }
    }
)

$internalRoutes = @($indexedRoutes | Where-Object { "/internal/*" -in $_.Paths })
$mcpRoutes = @($indexedRoutes | Where-Object {
    "/mcp" -in $_.Paths -and "/mcp/*" -in $_.Paths
})
if ($internalRoutes.Count -ne 1 -or $mcpRoutes.Count -ne 1) {
    throw "Caddy 必须保留唯一的内部阻断路由和 MCP 反代路由"
}
$internalRoute = $internalRoutes[0]
$mcpRoute = $mcpRoutes[0]
$spaRoutes = @($indexedRoutes | Where-Object {
    $_.Route.group -eq $internalRoute.Route.group -and $null -eq $_.Route.match
})
if ($spaRoutes.Count -ne 1) {
    throw "Caddy 必须保留唯一的无 matcher SPA fallback"
}
$spaRoute = $spaRoutes[0]

$internalHandler = $internalRoute.Route.handle[0].routes[0].handle[0]
$mcpHandler = $mcpRoute.Route.handle[0].routes[0].handle[0]
$mcpUpstreams = @($mcpHandler.upstreams | ForEach-Object { $_.dial })
if (
    $internalRoute.Route.group -ne $mcpRoute.Route.group -or
    $internalHandler.handler -ne "static_response" -or
    $internalHandler.status_code -ne 404 -or
    $mcpHandler.handler -ne "reverse_proxy" -or
    "mcp:8322" -notin $mcpUpstreams -or
    $internalRoute.Index -ge $mcpRoute.Index -or
    $mcpRoute.Index -ge $spaRoute.Index
) {
    throw "Caddy 必须依次互斥处理内部 404、MCP 8322 反代和 SPA fallback"
}

Write-Output "Compose MCP 边界检查通过"
