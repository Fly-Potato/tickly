# Tickly MCP 客户端接入与部署说明

本文说明如何把 Codex 配置为 Tickly 的远程 MCP 客户端，并排查最常见的鉴权环境变量错误。

## 前置条件

- Tickly MCP 已通过可信 HTTPS 入口暴露，例如 `https://todo.example.com/mcp`。
- API 与 MCP 服务端使用同一个 `TICKLY_MCP_TOKEN_SHA256`，值是原始 Token 的小写 SHA-256 摘要。
- Codex 启动进程能够读取原始 `TICKLY_MCP_TOKEN`。原始 Token 不写入服务器 `.env`、日志或 Git 仓库。

## 生成并配置 Token

在启动 Codex 的主机生成 Token，并把摘要交给服务器配置：

```powershell
$env:TICKLY_MCP_TOKEN = [Convert]::ToHexString(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()
$tokenHash = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes($env:TICKLY_MCP_TOKEN)
  )
).ToLowerInvariant()
$tokenHash
```

把输出的摘要设置为服务器端 `TICKLY_MCP_TOKEN_SHA256`，然后重建 API/MCP 服务。若 Codex 通过桌面进程启动，应将原始 Token 设置为用户环境变量并完全重启 Codex：

```powershell
[Environment]::SetEnvironmentVariable(
  "TICKLY_MCP_TOKEN", "<原始 Token>", "User"
)
```

## 配置 Codex

推荐使用 CLI 写入配置：

```shell
codex mcp add tickly --url https://todo.example.com/mcp --bearer-token-env-var TICKLY_MCP_TOKEN
```

等价的 `config.toml` 配置如下：

```toml
[mcp_servers.tickly]
url = "https://todo.example.com/mcp"
bearer_token_env_var = "TICKLY_MCP_TOKEN"
```

`bearer_token_env_var` 必须是环境变量名，而不是 Token 本身、Token 摘要或其他 Secret 值。

## 启动失败排查

如果错误类似：

```text
Environment variable <一串 64 位十六进制字符> ... is not set
```

说明配置把 Token/摘要误放进了 `bearer_token_env_var`。将它改成 `TICKLY_MCP_TOKEN`，并确认启动 Codex 的同一进程环境中该变量已设置。修改用户环境变量后必须完全重启 Codex；已有进程不会自动读取新环境变量。

如果该 Token 已出现在日志、截图、错误报告或配置提交中，应视为泄露：生成新 Token，更新服务器摘要，重建 API/MCP，再更新 Codex 主机环境变量。

## 最小验收

连接成功后先调用只读工具确认链路：

1. 列出工具，确认存在七个受限 Todo 工具。
2. 调用 `list_tasks` 或 `get_task`，确认能按当前账号读取任务。
3. 写工具仅在获得审批后验证；MCP 不提供删除工具，测试任务应通过 Web 删除。

远程连接必须经过可信 HTTPS 入口；仅有本地 HTTP Compose 验证不能视为生产部署完成。
