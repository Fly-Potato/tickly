param(
    [switch]$Traefik
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-JsonPropertyValue {
    param(
        [AllowNull()]
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Test-JsonProperty {
    param(
        [AllowNull()]
        [object]$Object,
        [string]$Name
    )

    return $null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]
}

function Get-RequiredJsonPropertyValue {
    param(
        [AllowNull()]
        [object]$Object,
        [string]$Name,
        [string]$ErrorMessage
    )

    $value = Get-JsonPropertyValue -Object $Object -Name $Name
    if ($null -eq $value) {
        throw $ErrorMessage
    }
    return $value
}

function Get-RequiredFirstJsonItem {
    param(
        [AllowNull()]
        [object]$Object,
        [string]$Name,
        [string]$ErrorMessage
    )

    $items = @(
        Get-RequiredJsonPropertyValue `
            -Object $Object `
            -Name $Name `
            -ErrorMessage $ErrorMessage
    )
    if ($items.Count -eq 0 -or $null -eq $items[0]) {
        throw $ErrorMessage
    }
    return $items[0]
}

function Get-CaddyLeafHandler {
    param(
        [object]$Route,
        [string]$RouteName
    )

    $outerHandler = Get-RequiredFirstJsonItem `
        -Object $Route `
        -Name "handle" `
        -ErrorMessage "Caddy $RouteName 路由缺少外层 handler"
    $nestedRoute = Get-RequiredFirstJsonItem `
        -Object $outerHandler `
        -Name "routes" `
        -ErrorMessage "Caddy $RouteName 路由缺少嵌套路由"
    return Get-RequiredFirstJsonItem `
        -Object $nestedRoute `
        -Name "handle" `
        -ErrorMessage "Caddy $RouteName 路由缺少业务 handler"
}

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
$services = Get-RequiredJsonPropertyValue `
    -Object $config `
    -Name "services" `
    -ErrorMessage "Compose 配置缺少 services"
$serviceNames = @($services.PSObject.Properties.Name)

foreach ($requiredService in @("api", "mcp", "web")) {
    if ($requiredService -notin $serviceNames) {
        throw "Compose 缺少服务：$requiredService"
    }
}
$apiService = Get-RequiredJsonPropertyValue -Object $services -Name "api" -ErrorMessage "Compose 缺少服务：api"
$mcpService = Get-RequiredJsonPropertyValue -Object $services -Name "mcp" -ErrorMessage "Compose 缺少服务：mcp"
$webService = Get-RequiredJsonPropertyValue -Object $services -Name "web" -ErrorMessage "Compose 缺少服务：web"

# API 与 MCP 只能通过 Compose 内部网络访问；基础模式由 Web/Caddy 独占宿主机入口，
# Traefik 模式则由外部网络接入 Web，三个服务都不得发布宿主机端口。
$apiPorts = Get-JsonPropertyValue -Object $apiService -Name "ports"
$mcpPorts = Get-JsonPropertyValue -Object $mcpService -Name "ports"
$webPorts = Get-JsonPropertyValue -Object $webService -Name "ports"
if ($null -ne $apiPorts -or $null -ne $mcpPorts) {
    throw "API 和 MCP 不得发布宿主机端口"
}
if (-not $Traefik -and $null -eq $webPorts) {
    throw "Web 必须是唯一发布宿主机端口的服务"
}
if ($Traefik -and $null -ne $webPorts) {
    throw "Traefik 模式下 Web 不得发布宿主机端口"
}

# 用户级 Token 只存在于 API 数据库和客户端；Compose 不得再注入共享摘要。
foreach ($serviceName in @("api", "mcp")) {
    $service = Get-RequiredJsonPropertyValue -Object $services -Name $serviceName -ErrorMessage "Compose 缺少服务：$serviceName"
    $environment = Get-RequiredJsonPropertyValue -Object $service -Name "environment" -ErrorMessage "Compose $serviceName 服务缺少 environment"
    if (Test-JsonProperty -Object $environment -Name "TICKLY_MCP_TOKEN_SHA256") {
        throw "$serviceName 不得继续配置静态 MCP Token 哈希"
    }
}
$mcpDependsOn = Get-RequiredJsonPropertyValue -Object $mcpService -Name "depends_on" -ErrorMessage "MCP 缺少 API 健康依赖"
$mcpApiDependency = Get-RequiredJsonPropertyValue -Object $mcpDependsOn -Name "api" -ErrorMessage "MCP 缺少 API 健康依赖"
$mcpApiCondition = Get-RequiredJsonPropertyValue -Object $mcpApiDependency -Name "condition" -ErrorMessage "MCP 缺少 API 健康依赖条件"
if ($mcpApiCondition -ne "service_healthy") {
    throw "MCP 必须等待 API healthy"
}
$webDependsOn = Get-RequiredJsonPropertyValue -Object $webService -Name "depends_on" -ErrorMessage "Web 缺少 MCP 健康依赖"
$webMcpDependency = Get-RequiredJsonPropertyValue -Object $webDependsOn -Name "mcp" -ErrorMessage "Web 缺少 MCP 健康依赖"
$webMcpCondition = Get-RequiredJsonPropertyValue -Object $webMcpDependency -Name "condition" -ErrorMessage "Web 缺少 MCP 健康依赖条件"
if ($webMcpCondition -ne "service_healthy") {
    throw "Web 必须等待 MCP healthy"
}

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
        $service = Get-RequiredJsonPropertyValue -Object $services -Name $serviceName -ErrorMessage "Compose 缺少服务：$serviceName"
        if ($null -ne (Get-JsonPropertyValue -Object $service -Name "build")) {
            throw "Traefik 模式下 $serviceName 不得保留本地 build"
        }
        $expectedImagePrefix = "ghcr.io/fly-potato/tickly-$serviceName"
        $imagePattern = "^$([regex]::Escape($expectedImagePrefix)):(?<tag>[^:@]+)$"
        $image = Get-RequiredJsonPropertyValue -Object $service -Name "image" -ErrorMessage "Traefik 模式下 $serviceName 缺少镜像"
        if ($image -notmatch $imagePattern) {
            throw "Traefik 模式下 $serviceName 必须使用 $expectedImagePrefix 的明确标签"
        }
        $imageTags += $Matches["tag"]
        $pullPolicy = Get-RequiredJsonPropertyValue -Object $service -Name "pull_policy" -ErrorMessage "Traefik 模式下 $serviceName 缺少 pull policy"
        if ($pullPolicy -ne "always") {
            throw "Traefik 模式下 $serviceName 必须在启动前检查目标镜像"
        }
    }
    if (@($imageTags | Select-Object -Unique).Count -ne 1) {
        throw "Traefik 模式下三个镜像必须使用同一标签"
    }

    $apiNetworkMap = Get-RequiredJsonPropertyValue -Object $apiService -Name "networks" -ErrorMessage "Traefik 模式下 API 缺少网络配置"
    $mcpNetworkMap = Get-RequiredJsonPropertyValue -Object $mcpService -Name "networks" -ErrorMessage "Traefik 模式下 MCP 缺少网络配置"
    $webNetworkMap = Get-RequiredJsonPropertyValue -Object $webService -Name "networks" -ErrorMessage "Traefik 模式下 Web 缺少网络配置"
    $apiNetworks = @($apiNetworkMap.PSObject.Properties.Name)
    $mcpNetworks = @($mcpNetworkMap.PSObject.Properties.Name)
    $webNetworks = @($webNetworkMap.PSObject.Properties.Name)
    if ($apiNetworks.Count -ne 1 -or "default" -notin $apiNetworks) {
        throw "Traefik 模式下 API 只能加入默认网络"
    }
    if ($mcpNetworks.Count -ne 1 -or "default" -notin $mcpNetworks) {
        throw "Traefik 模式下 MCP 只能加入默认网络"
    }
    if ($webNetworks.Count -ne 2 -or "default" -notin $webNetworks -or "traefik" -notin $webNetworks) {
        throw "Traefik 模式下 Web 必须同时加入默认网络与 Traefik 网络"
    }

    $networkMap = Get-RequiredJsonPropertyValue -Object $config -Name "networks" -ErrorMessage "Traefik 模式缺少网络声明"
    $traefikNetwork = Get-RequiredJsonPropertyValue -Object $networkMap -Name "traefik" -ErrorMessage "Traefik 模式缺少外部网络声明"
    $traefikExternal = Get-RequiredJsonPropertyValue -Object $traefikNetwork -Name "external" -ErrorMessage "Traefik 外部网络缺少 external 声明"
    $traefikNetworkName = Get-RequiredJsonPropertyValue -Object $traefikNetwork -Name "name" -ErrorMessage "Traefik 外部网络缺少名称"
    if (-not $traefikExternal -or [string]::IsNullOrWhiteSpace($traefikNetworkName)) {
        throw "Traefik 网络必须是具有明确名称的外部网络"
    }
    if (
        $null -ne (Get-JsonPropertyValue -Object $apiService -Name "labels") -or
        $null -ne (Get-JsonPropertyValue -Object $mcpService -Name "labels")
    ) {
        throw "API 和 MCP 不得配置 Traefik labels"
    }

    $labels = Get-RequiredJsonPropertyValue -Object $webService -Name "labels" -ErrorMessage "Traefik 模式下 Web 缺少 labels"
    $expectedLabels = @{
        "traefik.enable" = "true"
        "traefik.http.routers.tickly.tls" = "true"
        "traefik.http.routers.tickly.service" = "tickly-web"
        "traefik.http.services.tickly-web.loadbalancer.server.port" = "8080"
    }
    foreach ($labelName in $expectedLabels.Keys) {
        $actualValue = Get-JsonPropertyValue -Object $labels -Name $labelName
        if ($actualValue -ne $expectedLabels[$labelName]) {
            throw "Traefik label 不符合预期：$labelName"
        }
    }
    $dockerNetworkLabel = Get-JsonPropertyValue -Object $labels -Name "traefik.docker.network"
    if ($dockerNetworkLabel -ne $traefikNetworkName) {
        throw "Traefik label 必须选择 Compose 中声明的外部网络"
    }
    $hostRule = Get-JsonPropertyValue -Object $labels -Name "traefik.http.routers.tickly.rule"
    if ($hostRule -notmatch '^Host\(`[^`]+`\)$') {
        throw "Traefik router 必须使用单一非空域名的 Host rule"
    }
    foreach ($requiredLabel in @(
        "traefik.http.routers.tickly.entrypoints",
        "traefik.http.routers.tickly.tls.certresolver"
    )) {
        $labelValue = Get-JsonPropertyValue -Object $labels -Name $requiredLabel
        if ([string]::IsNullOrWhiteSpace($labelValue)) {
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
$composeName = Get-RequiredJsonPropertyValue -Object $config -Name "name" -ErrorMessage "Compose 配置缺少项目名称"
$webImage = "$composeName-web"
$webEnvironment = Get-RequiredJsonPropertyValue -Object $webService -Name "environment" -ErrorMessage "Web 服务缺少 environment"
$apiPort = Get-RequiredJsonPropertyValue -Object $webEnvironment -Name "TICKLY_API_PORT" -ErrorMessage "Web 服务缺少 API 端口"
$mcpPort = Get-RequiredJsonPropertyValue -Object $webEnvironment -Name "TICKLY_MCP_PORT" -ErrorMessage "Web 服务缺少 MCP 端口"
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
$caddyApps = Get-RequiredJsonPropertyValue -Object $caddyConfig -Name "apps" -ErrorMessage "Caddy 配置缺少 apps"
$caddyHttp = Get-RequiredJsonPropertyValue -Object $caddyApps -Name "http" -ErrorMessage "Caddy 配置缺少 HTTP app"
$caddyServerMap = Get-RequiredJsonPropertyValue -Object $caddyHttp -Name "servers" -ErrorMessage "Caddy HTTP app 缺少 servers"
$caddyServers = @($caddyServerMap.PSObject.Properties.Value)
$caddyRoutes = @(
    $caddyServers | ForEach-Object {
        @(Get-JsonPropertyValue -Object $_ -Name "routes")
    }
)
$indexedRoutes = @(
    for ($routeIndex = 0; $routeIndex -lt $caddyRoutes.Count; $routeIndex++) {
        $route = $caddyRoutes[$routeIndex]
        $routeMatch = Get-JsonPropertyValue -Object $route -Name "match"
        $routePaths = @(
            @($routeMatch) | ForEach-Object {
                @(Get-JsonPropertyValue -Object $_ -Name "path")
            }
        )
        [pscustomobject]@{
            Index = $routeIndex
            Route = $route
            Group = Get-JsonPropertyValue -Object $route -Name "group"
            Match = $routeMatch
            Paths = $routePaths
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
    $_.Group -eq $internalRoute.Group -and $null -eq $_.Match
})
if ($spaRoutes.Count -ne 1) {
    throw "Caddy 必须保留唯一的无 matcher SPA fallback"
}
$spaRoute = $spaRoutes[0]

$internalHandler = Get-CaddyLeafHandler -Route $internalRoute.Route -RouteName "内部阻断"
$mcpHandler = Get-CaddyLeafHandler -Route $mcpRoute.Route -RouteName "MCP 反代"
$mcpUpstreams = @(
    @(Get-JsonPropertyValue -Object $mcpHandler -Name "upstreams") |
        ForEach-Object { Get-JsonPropertyValue -Object $_ -Name "dial" }
)
$expectedMcpUpstream = "mcp:$mcpPort"
$internalHandlerType = Get-JsonPropertyValue -Object $internalHandler -Name "handler"
$internalStatusCode = Get-JsonPropertyValue -Object $internalHandler -Name "status_code"
$mcpHandlerType = Get-JsonPropertyValue -Object $mcpHandler -Name "handler"
if (
    $internalRoute.Group -ne $mcpRoute.Group -or
    $internalHandlerType -ne "static_response" -or
    $internalStatusCode -ne 404 -or
    $mcpHandlerType -ne "reverse_proxy" -or
    $expectedMcpUpstream -notin $mcpUpstreams -or
    $internalRoute.Index -ge $mcpRoute.Index -or
    $mcpRoute.Index -ge $spaRoute.Index
) {
    throw "Caddy 必须依次互斥处理内部 404、MCP 配置端口 $mcpPort 反代和 SPA fallback"
}

Write-Output "Compose MCP 边界检查通过"
