# Tickly MCP 客户端

Tickly MCP 通过无状态 Streamable HTTP `/mcp` 提供 Token 所属账号的受限 Todo 能力。每个账号可以创建多个具名个人访问令牌（PAT）；不同账号即使拥有相同的任务 `serial`，读取和写入也始终按 Token 所属账号隔离。

MCP 网关不访问 SQLite，也不保存用户或 Token 摘要。每个受保护请求都先把 Bearer Token 交给 API 验证；工具调用再把同一个 Bearer Token 透传给内部任务 API，由 API 再次验证并解析用户身份。客户端不能传入或伪造 `user_id`，MCP 也不能访问公开 Todo API。

## 工具清单

| 工具 | 用途 |
| --- | --- |
| `list_tasks` | 按关键词、`active` / `all` / 四种具体状态、主题、排序和 cursor 分页读取任务组 |
| `get_task` | 按账号内 `serial` 读取任务及直接子任务 |
| `list_topics` | 读取当前账号实际存在的精确主题值 |
| `find_parent_tasks` | 查找可以作为父任务的根任务 |
| `create_task` | 创建根任务或一层子任务 |
| `update_task` | 更新普通字段，不修改状态 |
| `set_task_status` | 切换 `new`、`in_progress`、`completed`、`cancelled` 状态 |

MCP 不提供删除、批量写入、任意 HTTP 转发或 SQL 工具。任务所有权、父子约束、流水号、事务和字段校验仍由 API 决定。

## 使用边界

- 用户可见任务身份是账号内 `serial`，例如 `#42`，不是数据库 `id`。
- 主题不确定时先使用 `list_topics`；父任务不确定时先使用 `find_parent_tasks`。
- 创建子任务使用 `parent_serial`，不要把父子关系写进描述文本。
- 普通字段更新使用 `update_task`，状态修改使用 `set_task_status`。
- `cancelled` 是可恢复的“已废弃”状态，不是删除；进入该状态会清空任务的 `completed_at`。
- 父任务进入 `cancelled` 时，API 会在服务端事务内级联废弃其 `new` / `in_progress` 直接子任务；`completed` / `cancelled` 子任务保持不变，恢复父任务也不会自动恢复子任务。MCP 客户端不得重复逐项修改子任务。
- AI 废弃父任务前必须先用 `get_task` 读取影响范围，明确列出父任务、会级联废弃的子任务、保持完成的子任务及“恢复父任务不会恢复子任务”，并等待用户确认。用户拒绝时不得调用写工具；写入后再次调用 `get_task`，只按实际结果报告。
- AI 恢复父任务前也必须提示不会自动恢复子任务并等待确认；写入后用 `get_task` 核验实际状态。
- 返回 `next_cursor` 时继续分页，不能只读取第一页后报告“全部”。
- `list_tasks` 默认使用 `status=active`，只展示 `new` / `in_progress`；已处理父任务可作为活动子任务的结构上下文。默认按优先级降序、同优先级创建时间降序排列，无优先级任务在最后；显式 `status=all` 仍返回全部四种状态。
- 需要检索时优先把关键词传给 `list_tasks.query`；它会匹配任务主题、标题和描述，并与状态、主题筛选按 AND 语义组合。
- 删除请求必须明确说明当前 MCP 没有删除能力，不得用清空字段或改状态伪造删除。

## 创建与维护 Token

1. 使用 CLI 创建的账号登录 Web，进入 `/settings`。
2. 为设备或用途填写 Token 名称，并选择 90 天、365 天（默认）或永不过期。
3. 创建成功后立即把原始 Token 保存到对应客户端的秘密存储。原始值只显示一次；列表、日志和数据库都不能用来找回它。

同一用户可以同时保留多个 Token，适合为家中电脑、办公电脑和自动化进程分别签发。轮换时先创建新 Token、更新并验证目标客户端，再在设置页撤销旧 Token；撤销只影响选中的凭据，不影响该用户的其他 Token。永不过期 Token 仍应按设备边界定期轮换。

已撤销、已到期或属于停用账号的 Token 都会在后续请求中得到 `401 authentication_required`。修改 Web 密码不会自动撤销 MCP Token；需要失效设备凭据时必须在设置页单独撤销。

## Codex 配置

在同一环境中用 Codex CLI 添加远程 Streamable HTTP 服务：

```shell
codex mcp add tickly --url https://tickly.example.com/mcp --bearer-token-env-var TICKLY_MCP_TOKEN
```

将示例域名替换为真实可信的 HTTPS 入口。远程 Bearer Token 使用 `--bearer-token-env-var`；启动 Codex 的进程必须能读取该环境变量。

先通过客户端的安全输入或秘密管理器把设置页返回的原始值注入 `TICKLY_MCP_TOKEN`，再运行上述命令。不要把原始 Token 直接写进命令行参数、Shell 历史、Codex 配置、服务器环境或日志。本命令语法已按当前 `codex mcp add --help` 核对；URL 必须包含实际 Streamable HTTP 路径 `/mcp`。

连接后先执行只读工具 smoke，再按客户端的写操作审批策略验证写工具。公网 HTTPS、Host/Origin 白名单和生产部署验收见 [VPS 部署说明](mcp-client-deployment.md)。
