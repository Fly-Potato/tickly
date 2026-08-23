# Tickly MCP 客户端

Tickly MCP 通过无状态 Streamable HTTP `/mcp` 提供当前账号的受限 Todo 能力。MCP 只调用 API 的内部契约，不直接访问 SQLite，也不能访问公开 Todo API。

## 工具清单

| 工具 | 用途 |
| --- | --- |
| `list_tasks` | 按状态、主题、排序和 cursor 分页读取任务组 |
| `get_task` | 按账号内 `serial` 读取任务及直接子任务 |
| `list_topics` | 读取当前账号实际存在的精确主题值 |
| `find_parent_tasks` | 查找可以作为父任务的根任务 |
| `create_task` | 创建根任务或一层子任务 |
| `update_task` | 更新普通字段，不修改状态 |
| `set_task_status` | 切换 `new`、`in_progress`、`completed` 状态 |

MCP 不提供删除、批量写入、任意 HTTP 转发或 SQL 工具。任务所有权、父子约束、流水号、事务和字段校验仍由 API 决定。

## 使用边界

- 用户可见任务身份是账号内 `serial`，例如 `#42`，不是数据库 `id`。
- 主题不确定时先使用 `list_topics`；父任务不确定时先使用 `find_parent_tasks`。
- 创建子任务使用 `parent_serial`，不要把父子关系写进描述文本。
- 普通字段更新使用 `update_task`，状态修改使用 `set_task_status`。
- 返回 `next_cursor` 时继续分页，不能只读取第一页后报告“全部”。
- 删除请求必须明确说明当前 MCP 没有删除能力，不得用清空字段或改状态伪造删除。

## Token 配置

在启动 Codex 的主机生成原始 Token，并只输出其小写 SHA-256 摘要。PowerShell 示例：

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

POSIX shell 示例：

```bash
export TICKLY_MCP_TOKEN="$(openssl rand -hex 32)"
printf %s "$TICKLY_MCP_TOKEN" | sha256sum | cut -d ' ' -f 1
```

将摘要配置到服务器根 `.env` 的 `TICKLY_MCP_TOKEN_SHA256`。API 和 MCP 必须使用同一个摘要；原始 `TICKLY_MCP_TOKEN` 只保存在调用客户端的安全环境中。

## Codex 配置

在同一环境中用 Codex CLI 添加远程 Streamable HTTP 服务：

```shell
codex mcp add tickly --url https://tickly.example.com/mcp --bearer-token-env-var TICKLY_MCP_TOKEN
```

将示例域名替换为真实可信的 HTTPS 入口。远程 Bearer Token 使用 `--bearer-token-env-var`；启动 Codex 的进程必须能读取该环境变量。

连接后先执行只读工具 smoke，再按客户端的写操作审批策略验证写工具。公网 HTTPS、Host/Origin 白名单和生产部署验收见 [VPS 部署说明](mcp-client-deployment.md)。
