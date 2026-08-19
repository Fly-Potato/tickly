param(
    [switch]$Traefik
)

$ErrorActionPreference = "Stop"

# 解析 Compose 的最终模型，避免只靠文本匹配漏掉环境变量插值或默认值问题。
$composeArguments = @("compose")
if ($Traefik) {
    $composeArguments += @("-f", "compose.yaml", "-f", "compose.traefik.yaml")
}
$composeArguments += @("config", "--format", "json")
$rawConfig = & docker @composeArguments
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

# API 与 MCP 只能通过 Compose 内部网络访问；基础模式由 Web/Caddy 独占宿主机入口，
# Traefik 模式则由外部网络接入 Web，三个服务都不得发布宿主机端口。
if ($null -ne $config.services.api.ports -or $null -ne $config.services.mcp.ports) {
    throw "API 和 MCP 不得发布宿主机端口"
}
if (-not $Traefik -and $null -eq $config.services.web.ports) {
    throw "Web 必须是唯一发布宿主机端口的服务"
}
if ($Traefik -and $null -ne $config.services.web.ports) {
    throw "Traefik 模式下 Web 不得发布宿主机端口"
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

if ($Traefik) {
    $imageTags = @()
    foreach ($serviceName in @("api", "mcp", "web")) {
        $service = $config.services.PSObject.Properties[$serviceName].Value
        if ($null -ne $service.build) {
            throw "Traefik 模式下 $serviceName 不得保留本地 build"
        }
        $expectedImagePrefix = "ghcr.io/fly-potato/tickly-$serviceName"
        $imagePattern = "^$([regex]::Escape($expectedImagePrefix)):(?<tag>[^:@]+)$"
        if ($service.image -notmatch $imagePattern) {
            throw "Traefik 模式下 $serviceName 必须使用 $expectedImagePrefix 的明确标签"
        }
        $imageTags += $Matches["tag"]
        if ($service.pull_policy -ne "always") {
            throw "Traefik 模式下 $serviceName 必须在启动前检查目标镜像"
        }
    }
    if (@($imageTags | Select-Object -Unique).Count -ne 1) {
        throw "Traefik 模式下三个镜像必须使用同一标签"
    }

    $apiNetworks = @($config.services.api.networks.PSObject.Properties.Name)
    $mcpNetworks = @($config.services.mcp.networks.PSObject.Properties.Name)
    $webNetworks = @($config.services.web.networks.PSObject.Properties.Name)
    if ($apiNetworks.Count -ne 1 -or "default" -notin $apiNetworks) {
        throw "Traefik 模式下 API 只能加入默认网络"
    }
    if ($mcpNetworks.Count -ne 1 -or "default" -notin $mcpNetworks) {
        throw "Traefik 模式下 MCP 只能加入默认网络"
    }
    if ($webNetworks.Count -ne 2 -or "default" -notin $webNetworks -or "traefik" -notin $webNetworks) {
        throw "Traefik 模式下 Web 必须同时加入默认网络与 Traefik 网络"
    }

    $traefikNetwork = $config.networks.PSObject.Properties["traefik"].Value
    if ($null -eq $traefikNetwork -or -not $traefikNetwork.external -or [string]::IsNullOrWhiteSpace($traefikNetwork.name)) {
        throw "Traefik 网络必须是具有明确名称的外部网络"
    }
    if ($null -ne $config.services.api.labels -or $null -ne $config.services.mcp.labels) {
        throw "API 和 MCP 不得配置 Traefik labels"
    }

    $labels = $config.services.web.labels
    $expectedLabels = @{
        "traefik.enable" = "true"
        "traefik.http.routers.tickly.tls" = "true"
        "traefik.http.routers.tickly.service" = "tickly-web"
        "traefik.http.services.tickly-web.loadbalancer.server.port" = "8080"
    }
    foreach ($labelName in $expectedLabels.Keys) {
        $actualValue = $labels.PSObject.Properties[$labelName].Value
        if ($actualValue -ne $expectedLabels[$labelName]) {
            throw "Traefik label 不符合预期：$labelName"
        }
    }
    if ($labels.PSObject.Properties["traefik.docker.network"].Value -ne $traefikNetwork.name) {
        throw "Traefik label 必须选择 Compose 中声明的外部网络"
    }
    $hostRule = $labels.PSObject.Properties["traefik.http.routers.tickly.rule"].Value
    if ($hostRule -notmatch '^Host\(`[^`]+`\)$') {
        throw "Traefik router 必须使用单一非空域名的 Host rule"
    }
    foreach ($requiredLabel in @(
        "traefik.http.routers.tickly.entrypoints",
        "traefik.http.routers.tickly.tls.certresolver"
    )) {
        if ([string]::IsNullOrWhiteSpace($labels.PSObject.Properties[$requiredLabel].Value)) {
            throw "Traefik label 不得为空：$requiredLabel"
        }
    }

    Write-Output "Compose Traefik 边界检查通过"
    exit 0
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
$expectedMcpUpstream = "mcp:$mcpPort"
if (
    $internalRoute.Route.group -ne $mcpRoute.Route.group -or
    $internalHandler.handler -ne "static_response" -or
    $internalHandler.status_code -ne 404 -or
    $mcpHandler.handler -ne "reverse_proxy" -or
    $expectedMcpUpstream -notin $mcpUpstreams -or
    $internalRoute.Index -ge $mcpRoute.Index -or
    $mcpRoute.Index -ge $spaRoute.Index
) {
    throw "Caddy 必须依次互斥处理内部 404、MCP 配置端口 $mcpPort 反代和 SPA fallback"
}

Write-Output "Compose MCP 边界检查通过"
