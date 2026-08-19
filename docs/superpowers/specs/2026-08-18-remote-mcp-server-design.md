# Tickly 远程 MCP 服务设计

## 背景

Tickly 当前通过 `apps/api` 提供受 JWT 保护的 Todo API，通过 `apps/web`
提供浏览器工作区。任务服务已经覆盖账号内流水号、三种状态、主题、优先级、
可选截止时间、一层父子任务、筛选、排序和 cursor 分页；AI 能力尚未实现。

本次新增一个与现有服务共同部署的远程 MCP Server，让 Codex 能读取和修改
当前唯一账号的任务。MCP 是现有任务能力的受限适配层，不替代 Web API，也不
实现自然语言任务草稿。

## 目标

- Codex 通过公网 HTTPS 和 Streamable HTTP 连接 Tickly MCP。
- MCP 与 API 使用独立容器，并通过 Compose 内网通信。
- 使用独立的长期 Bearer Token，不复用 Web access token 或 refresh token。
- 提供任务查询、创建、编辑和状态流转工具，但不提供删除工具。
- 复用 API 的任务业务规则、事务和所有权校验，不让 MCP 直接访问 SQLite。
- 保持现有 Web 登录、公开 Todo API 和单进程写 SQLite 的行为不变。
- 对认证、工具 Schema、错误映射、内部契约和真实 HTTP 链路建立自动化测试。

## 非目标

- 不实现 OAuth、动态客户端注册或多用户 Token 管理。
- 不提供任务删除、批量写入、通用 SQL、任意 HTTP 转发或管理型工具。
- 不增加 MCP Resources、Prompts、交互式 Apps 或 AI 模型调用。
- 不从 OpenAPI 自动生成工具，也不构建通用 MCP 网关或插件系统。
- 不让 MCP 容器共享 SQLite volume、ORM Session 或 API 进程内对象。
- 不为未来供应商、其他 MCP 客户端或多实例部署提前增加抽象。

## 已确认决策

| 项目 | 决策 |
| --- | --- |
| 首个客户端 | Codex |
| 部署方式 | 与 API 同一 Compose，使用独立 MCP 容器 |
| 公网传输 | Streamable HTTP，路径 `/mcp` |
| MCP 框架 | 官方 MCP Python SDK v2，不使用独立 FastMCP |
| 客户端认证 | 独立长期 Bearer Token |
| Token 流向 | 同一个 Token 从 Codex 经 MCP 转发到 API 内部路由 |
| 账号映射 | 固定映射到唯一且启用的 Tickly 账号 |
| 写操作 | 创建、编辑、状态流转 |
| 删除 | 不提供 |

选择官方 MCP Python SDK v2 的原因是当前稳定版直接支持最新协议线、
Streamable HTTP、类型化工具和内存客户端测试。当前 PyPI FastMCP 3.4.7
仍依赖 `mcp>=1.24,<2.0`；Tickly 首版不需要其代理、组合或高级认证抽象，
因此不承担额外依赖面和后续主版本迁移成本。

## 总体架构

```text
Codex
  │ HTTPS + Authorization: Bearer <token>
  ▼
Caddy / Web container
  ├── /api/*  ─────────────────────────────► api container
  └── /mcp、/mcp/* ─► mcp container
                            │ Compose 内网
                            │ Authorization: Bearer <same token>
                            ▼
                    /internal/mcp/v1/*
                            │
                            ▼
                    Task Service → SQLite
```

新增 `apps/mcp`，作为独立的 Python 3.13 uv 项目，拥有自己的
`pyproject.toml`、`uv.lock`、应用代码、测试和 Dockerfile。生产依赖使用
`mcp>=2,<3`、HTTP 客户端和必要的配置库；协议测试直接使用 SDK 客户端，未引入
MCP CLI 或 Inspector 依赖。最终锁文件将 `mcp` 固定为 `2.0.0`。

MCP 容器只负责协议、工具 Schema、入口认证、参数适配、API 调用和安全错误
映射。任务合法性、账号所有权、父子关系、流水号分配、完成时间和事务仍由
`apps/api` 决定。

## 网络与路由边界

- Caddy 继续作为唯一发布宿主机端口的容器。
- Caddy 将 `/mcp` 及其子路径反向代理到 MCP 容器，并保留
  `Authorization`、MCP 协议头和 request ID。
- API 与 MCP 容器仅通过 Compose 内网暴露端口，不发布宿主机端口。
- API 新增 `/internal/mcp/v1` 路由树，但 Caddy 不代理 `/internal/*`。
- Caddy 在静态站点 fallback 之前显式拒绝 `/internal/*`，确保这类请求返回
  `404`，而不是误返回 Web 的 `index.html`。
- 内部网络不是认证边界；所有内部 MCP 请求仍必须携带并校验 Token。
- MCP 使用独立 `/health` 和 `/ready` 供 Compose 健康检查，这两个路径不经
  Caddy 暴露。`/health` 只表示进程存活，`/ready` 检查配置和 API readiness。
- MCP 使用无状态 Streamable HTTP，并限制请求体大小；存在 `Origin` 时只接受
  配置的同源值，无 `Origin` 的 Codex 非浏览器请求正常接受。
- Host/Origin 白名单接受普通 IPv4、IPv6 literal 与 DNS 主机名，但不接受带
  interface scope 的 link-local IPv6；如未来需要此能力，应按 RFC 6874 独立设计。

当前仓库的 Caddy 只监听 `:8080`，尚未实现生产 HTTPS。远程 MCP 上线必须满足
以下二选一前置条件：由现有可信入口在 Caddy 前终止 TLS，或在后续 VPS 部署中
为 Caddy 配置域名、证书及 `80/443` 端口。MCP 实现只负责新增路由，不把完整
VPS TLS 生命周期混入本功能。自动化 Compose 测试可以使用本机 HTTP，真实 Codex
验收必须使用 HTTPS；TLS 未完成时不得声称远程生产部署已就绪。

## Bearer Token 与账号绑定

运维人员生成至少 256 bit 的随机 Token。原始 Token 只写入 Codex 所在机器的
环境变量，例如 `TICKLY_MCP_TOKEN`；服务器只配置其小写 SHA-256 十六进制值
`TICKLY_MCP_TOKEN_SHA256`。

当前本地 Codex CLI 已确认的配置命令：

```shell
codex mcp add tickly --url https://tickly.example.com/mcp --bearer-token-env-var TICKLY_MCP_TOKEN
```

原始 Token 必须存在于启动 Codex 的环境中。写操作审批继续由 Codex 客户端策略
控制；服务端文档不假设或固定未确认的客户端审批配置字段。

认证按以下顺序执行：

1. MCP ASGI 中间件在协议初始化、工具发现和工具调用前解析 Bearer Token。
2. MCP 对 Token 做 SHA-256，并以常量时间比较配置的哈希；失败返回 `401`，
   且不得返回工具列表。
3. 工具调用把当前请求中的原 Token 原样转发给 API 内部路由，不写入应用状态、
   缓存或日志。
4. API 再次独立校验相同哈希，并查找唯一且启用的账号。
5. 没有账号、存在多个账号、账号停用或哈希配置不可用时全部失败关闭。

MCP 应用在生产环境缺少合法的 64 位十六进制哈希时拒绝启动。API 的 MCP 配置
可以缺省，以便普通 Web/API 独立运行；缺省时内部 MCP 路由始终拒绝请求。
生产 Compose 要求同一哈希同时注入 API 与 MCP。Token 轮换通过生成新 Token、
更新哈希并重启两个容器完成；首版不支持新旧 Token 并存。

## API 内部契约

内部接口使用 MCP 友好的账号内 `serial`，不要求 MCP 操作 UUID：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/internal/mcp/v1/tasks` | 筛选、排序和分页列出任务组 |
| `POST` | `/internal/mcp/v1/tasks` | 创建根任务或子任务 |
| `GET` | `/internal/mcp/v1/tasks/topics` | 列出主题 |
| `GET` | `/internal/mcp/v1/tasks/parent-options` | 查找根任务候选 |
| `GET` | `/internal/mcp/v1/tasks/{serial}` | 获取任务及直接子任务 |
| `PATCH` | `/internal/mcp/v1/tasks/{serial}` | 更新字段或状态 |

不提供 `DELETE`。内部 Schema 复用公开 Schema 的字段、枚举、长度、时间和分页
规则，只在引用字段上使用 `parent_serial` 替代 `parent_id`。API 在当前账号范围内
解析 serial，并在现有事务边界中完成父级校验，不能先在 MCP 中解析 UUID 再写入。
内部 router 使用 `include_in_schema=False`，不进入面向 Web 客户端的公开
`/openapi.json`。

普通 `/api/v1/tasks` 继续只接受 Web JWT。MCP Token 不能直接访问公开任务 API，
避免绕过 MCP 工具白名单调用删除端点。

## MCP 工具契约

| 工具 | 主要输入 | 结果 | 注解 |
| --- | --- | --- | --- |
| `list_tasks` | `status`、`topic`、`sort`、`order`、`cursor`、`limit` | 完整根任务组和下一页 cursor | 只读、幂等 |
| `get_task` | `serial` | 任务详情和直接子任务 | 只读、幂等 |
| `list_topics` | 无 | 当前账号的精确主题集合 | 只读、幂等 |
| `find_parent_tasks` | `query`、`cursor`、`limit` | 可作为父任务的根任务 | 只读、幂等 |
| `create_task` | `title`、`topic`、可选描述/优先级/截止时间/父流水号 | 新任务 | 写入、非幂等 |
| `update_task` | `serial` 和字段 patch | 更新后的任务 | 写入 |
| `set_task_status` | `serial`、`status` | 更新后的任务 | 写入、非幂等 |

工具遵守以下规则：

- 不注册任何删除工具。
- `update_task` 不接受 `status`，状态只能通过 `set_task_status` 修改。
- patch 中省略字段表示保持不变；`priority`、`due_at`、`parent_serial` 显式传
  `null` 表示清空。`title`、`description`、`topic` 不可清空。
- 截止时间必须包含时区，并由 API 转换为 UTC。
- `parent_serial` 必须指向当前账号的根任务；任务仍只允许一层父子关系。
- `id`、`serial`、`completed_at`、`user_id` 等服务端字段不可写入。
- 列表延续现有根任务组分页语义，不拆散父子分组。
- 每个工具返回结构化 JSON 和简短文本摘要；摘要不得替代机器可读结果。
- 工具注解准确标记只读、写入、破坏性和幂等属性。服务端 `instructions`
  在前 512 字符内说明：引用不明确时先读后写、使用 serial、无删除能力、写操作
  受 Codex 审批策略约束。

现有任务更新即使字段值相同也会推进 `updated_at`，因此 `update_task` 和
`set_task_status` 都不得声明 `idempotentHint=true`。这不会改变状态字段的最终值，
但会产生可观察的更新时间副作用。

## HTTP 客户端与超时

MCP 为进程生命周期维护一个异步 HTTP client，连接目标只能来自经过校验的
内部 API base URL。共享 client 使用显式连接和总请求超时，并透传协议请求 ID。

MCP 不自动重试写请求，因为超时可能发生在 API 已经提交事务之后，重试会重复
创建或覆盖任务。首版读取请求也不隐式重试，保持失败行为一致，由 Codex 决定
是否再次调用。响应体设定上限，且只解析预期 JSON 契约。

## 错误契约

| 场景 | MCP 错误码 | 行为 |
| --- | --- | --- |
| 缺少或错误 Token | `authentication_required` | HTTP `401`，不暴露工具 |
| 账号不可唯一解析或已停用 | `mcp_account_unavailable` | 工具失败，不回显账号信息 |
| 任务不存在或不属于账号 | `task_not_found` | 不区分具体原因 |
| cursor 无效 | `invalid_cursor` | 不回显 cursor |
| 父子关系非法 | `invalid_task_relationship` | 不泄漏目标存在性或归属 |
| 输入 Schema 非法 | `validation_error` | 返回稳定字段错误，不返回内部栈 |
| API 超时或不可达 | `upstream_unavailable` | 不自动重试，不泄漏内部 URL |
| API 返回未知响应 | `upstream_contract_error` | 记录安全诊断，向 Codex 返回稳定错误 |

API 已有稳定错误码保持原义。MCP 仅进行协议层映射，不根据错误文本推断业务
结果。任何无法分类的异常都失败关闭，并由统一异常边界移除凭据、请求正文、
内部路径和堆栈。

## 日志与敏感数据

客户端提供且通过校验的 `X-Request-ID` 会由 MCP 返回并透传给 API；缺失或非法时
由 MCP 生成替代值。为防止客户端把 Token 或任务文本注入日志，MCP 另行生成
服务端日志关联 ID，并让同一次访问事件与工具事件复用它。MCP 结构化日志只记录：
工具名、结果、耗时、HTTP 状态、稳定错误码和服务端日志关联 ID。

以下内容禁止写日志：

- Authorization header、原始 Token 或 Token 哈希；
- 任务标题、描述、主题、截止时间和完整工具参数；
- 内部 API 完整 URL、响应正文和异常栈中的敏感值；
- Codex 本机配置或环境变量内容。

首版不新增数据库审计表。任务本身已有 `created_at` 和 `updated_at`，运行诊断使用
结构化应用日志；需要长期操作审计时再单独设计。

## 配置

MCP 应用至少包含以下配置：

| 环境变量 | 用途 |
| --- | --- |
| `TICKLY_MCP_ENVIRONMENT` | development、test 或 production |
| `TICKLY_MCP_HOST` | MCP 进程监听地址 |
| `TICKLY_MCP_PORT` | MCP 进程监听端口，默认与 API 端口不同 |
| `TICKLY_MCP_API_BASE_URL` | Compose 内网 API origin |
| `TICKLY_MCP_TOKEN_SHA256` | 共享 Token 哈希 |
| `TICKLY_MCP_ALLOWED_HOSTS` | 允许的公网 Host 列表 |
| `TICKLY_MCP_ALLOWED_ORIGINS` | 存在 Origin header 时允许的公网同源列表 |
| `TICKLY_MCP_CONNECT_TIMEOUT_SECONDS` | API 连接超时 |
| `TICKLY_MCP_REQUEST_TIMEOUT_SECONDS` | API 总请求超时 |
| `TICKLY_MCP_REQUEST_ID_HEADER` | 协议请求 ID header，默认 `X-Request-ID` |
| `TICKLY_MCP_LOG_LEVEL` | MCP 日志级别 |
| `TICKLY_MCP_LOG_JSON` | 是否输出 JSON 日志 |
| `TICKLY_MCP_MAX_REQUEST_BODY_SIZE` | MCP 请求体与上游响应体上限 |

API 增加可选的 `TICKLY_MCP_TOKEN_SHA256`。根 `.env.example` 和 MCP 本地
`.env.example` 只提供说明和占位符，不包含可用 Token。README 记录 Token 生成、
哈希、轮换、Codex 配置和故障排查步骤。

## 测试策略

### MCP 单元与协议测试

- 缺少、格式错误和哈希不匹配的 Bearer Token 均无法初始化或发现工具。
- 七个工具的名称、输入 Schema、结构化输出和注解与设计一致，且不存在删除工具。
- 使用官方 SDK 内存 Client 验证工具调用，无需真实网络。
- 使用 HTTP mock 验证参数映射、Authorization 和 request ID 透传。
- 验证 patch 的省略与显式 `null`、带时区截止时间、cursor 和父流水号。
- 验证读取和写入都不会自动重试。
- 验证超时、未知状态码、非 JSON、超大响应和契约漂移映射为安全错误。
- 验证日志不包含 Token、任务文本、完整参数和内部 URL。

### API 测试

- 内部路由在 MCP 配置缺失、Token 缺失和 Token 错误时失败关闭。
- 正确 Token 只能绑定唯一且启用的账号；零账号、多账号和停用账号均拒绝。
- MCP Token 不能访问普通 `/api/v1/tasks`。
- 内部路由按 serial 读取和更新，且不能枚举其他账号任务。
- 创建、patch、状态时间、父子关系、流水号回滚和 cursor 继续复用现有业务语义。
- 内部路由没有 DELETE，公开 OpenAPI 不意外暴露内部契约。

### 集成与容器验证

- 通过真实 Streamable HTTP 完成 initialize、tools/list 和代表性 tools/call。
- 错误 Token 不能列出工具；正确 Token 可读取、创建、编辑和完成任务。
- Compose 中 Web、API、MCP 健康检查通过，且只有 Web/Caddy 发布端口。
- 公网 `/mcp` 可达，公网 `/internal/mcp/v1/*` 不可达。
- MCP 创建的数据可在 Web/API 中读取，容器重启后仍存在。
- 现有 Web JWT、Todo API、`/health` 和 `/ready` 行为不回归。

至少执行新增 MCP 测试、`mise exec -- pnpm test:api`、Compose 配置解析和镜像
构建。真实 Codex 连接作为最终手工 smoke；未实际执行的检查不得描述为通过。

## 部署与轮换流程

首次启用：

1. 生成高熵原始 Token，并在本地计算 SHA-256。
2. 将原始 Token 只放入 Codex 主机的 `TICKLY_MCP_TOKEN`。
3. 将哈希放入生产环境配置，供 API 和 MCP 容器读取。
4. 构建镜像，先启动 API，再等待 API ready，最后启动 MCP 和 Web/Caddy。
5. 确认 MCP ready 后，在 Codex 中添加远程 server 并执行只读 smoke。
6. 经 Codex 写操作审批后创建测试任务，再通过 Web 核对并手工清理。

轮换时生成新 Token 和哈希，更新服务端配置并重启 API/MCP，然后更新 Codex
环境变量。旧 Token 在容器采用新哈希后立即失效。若需要无中断双 Token 轮换，
应作为后续独立设计，不在首版中隐式支持。

## 验收标准

- Codex 能通过 HTTPS `/mcp` 和环境变量 Bearer Token 成功连接。
- 未认证客户端无法初始化、获取工具列表或调用工具。
- 工具列表恰好包含七个已定义工具，不包含删除或任意转发能力。
- Codex 能按 serial 查询任务，创建根任务和一层子任务，编辑可写字段并切换状态。
- 所有写入继续遵守账号所有权、流水号、父子关系、UTC 和事务回滚规则。
- MCP Token 不能调用公开 Todo API，内部路由不能从 Caddy 公网访问。
- API 或 MCP 故障不会影响 Web 静态页面；API 恢复后 MCP 可重新就绪。
- 日志和错误响应不包含 Token、任务正文、内部 URL 或异常栈。
- 自动化测试覆盖认证、工具契约、内部 API、错误映射和真实 HTTP 协议链路。
