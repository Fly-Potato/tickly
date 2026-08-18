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

Write-Output "Compose MCP 边界检查通过"
