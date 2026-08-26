# Tickly 多用户 MCP 与账号设置设计

日期：2026-08-25

## 目标

- 保留管理员通过 CLI 创建账号的现有入口，但允许创建多个用户名唯一的账号。
- 为已登录用户提供设置页，使其可以修改自己的密码。
- 允许每个用户创建多个具名 MCP Token，并分别查看状态和撤销。
- 让每个 MCP Token 只能访问所属用户的任务，并继续复用现有任务所有权约束。
- 保持 MCP 不直接访问 SQLite，API 继续作为用户身份和数据授权的唯一权威。

## 非目标

- 不增加 Web 自助注册、邀请、管理员后台或其他用户管理页面。
- 不允许用户修改用户名，也不在本阶段增加时区编辑能力。
- 不实现 OAuth 授权服务器、第三方客户端注册或细粒度 MCP scope。
- 不改变现有七个 MCP Todo 工具的业务能力，不增加删除工具。
- 不把 MCP Token 用作公开 Web API 的登录凭据。

## 总体架构

采用用户级不透明 Personal Access Token。API 生成并保存 Token 摘要，MCP 只负责把请求凭据交给 API 验证，并在工具调用时透传同一凭据。

```text
Web 登录用户
  └─ Web JWT → API → 创建、列出、撤销自己的 MCP Token

MCP 客户端
  └─ 用户级 Bearer Token
       → MCP 传输层
       → API 内部验证端点
       → MCP 协议处理
       → 工具调用透传同一 Token
       → API 内部任务端点
       → Token 对应 user_id
       → 现有任务所有权隔离
```

API 是唯一的身份权威。MCP 不保存用户、Token 摘要或数据库连接，也不接受客户端提供的 `user_id`。内部任务接口必须从 Token 重新解析用户，不能信任 MCP 传入的身份字段。

### 传输层认证

- `/health` 与 `/ready` 继续公开，供容器探针使用。
- 每个受保护的 `/mcp` 请求在进入 MCP 协议解析前必须带 Bearer Token。
- MCP 传输中间件将 Token 交给 API 的内部验证端点；无效、过期、已撤销或所属账号停用时统一返回 `401`。
- 验证成功后，明文 Token 只保留在当前请求上下文中。
- 工具调用把同一 Token 透传给内部任务 API，内部任务 API 再验证一次并解析 `user_id`。
- 二次验证用于覆盖“传输验证后、任务执行前发生撤销”的竞态，同时避免 MCP 伪造用户身份。

MCP 请求因此会多一次轻量内部 HTTP 验证。当前优先保证身份边界清晰和撤销立即生效，不引入正向认证缓存；只有获得真实延迟和负载证据后才评估缓存，并且任何缓存都不能削弱撤销语义。

### 网络边界

- 公网继续只暴露 `/mcp` 和 `/api/v1/*`。
- Caddy 继续明确拒绝 `/internal/*`，内部验证和任务接口只能由容器网络中的 MCP 调用。
- 公开 Web API 的认证依赖只接受 Web access token。
- 内部 MCP API 的认证依赖只接受 `tickly_mcp_` 格式的用户 Token。

## MCP Token 数据模型

新增 `mcp_tokens` 表：

| 字段 | 类型与约束 | 含义 |
| --- | --- | --- |
| `id` | UUID 主键 | Token 的随机公开定位标识 |
| `user_id` | 外键，非空，级联删除，索引 | 所属用户 |
| `name` | 1 至 64 字符，非空 | 用户填写的设备或用途名称 |
| `token_hash` | 64 位小写 SHA-256，唯一 | 完整 Token 的不可逆摘要 |
| `expires_at` | UTC 时间，可空 | 空值表示永不过期 |
| `revoked_at` | UTC 时间，可空 | 非空表示已撤销且不可恢复 |
| `last_used_at` | UTC 时间，可空 | 最近一次成功使用时间 |
| `created_at` | UTC 时间，非空 | 创建时间 |

Token 名称只是标签，允许同一用户重名。列表通过名称、创建时间和随机 Token ID 区分记录，避免引入过期或撤销后名称能否复用的额外规则。

### Token 格式与生成

Token 使用以下格式：

```text
tickly_mcp_<随机token_id>.<随机secret>
```

- `token_id` 是不可预测的随机 UUID，不包含用户信息。
- `secret` 使用密码学安全随机数生成器产生至少 256 位随机熵。
- 数据库保存完整 Token 的 SHA-256 摘要，不保存 secret 或完整 Token。
- 验证时先解析随机 `token_id` 定位记录，再恒定时间比较完整 Token 摘要。
- 随机 secret 已具有足够熵，不使用面向低熵密码的 Argon2，避免为每次 MCP 调用引入不必要成本。

只有创建成功响应返回一次完整 Token。任何后续列表、错误、日志或审计字段都不得包含原始 Token 或 `token_hash`。

### 有效期与状态

创建时只支持三个明确选项：

- 90 天；
- 365 天，默认值；
- 永不过期。

状态由服务端投影为：

- `active`：未撤销且未到期；
- `expired`：已到期；
- `revoked`：已撤销，撤销优先于到期展示。

撤销只写入 `revoked_at`，不物理删除，且不能恢复。`last_used_at` 最多每小时条件更新一次，避免高频工具调用持续写 SQLite；该字段更新失败不得把已经授权的任务操作误报为认证失败。

## API 契约

### Web Token 管理 API

以下接口都使用现有 `CurrentUser` Web JWT 依赖，只能访问当前用户自己的记录。

#### `GET /api/v1/mcp-tokens`

返回当前用户的 Token 元数据，按创建时间倒序排列。响应包含 `id`、`name`、`status`、`created_at`、`last_used_at` 和 `expires_at`，不包含摘要或明文 Token。

#### `POST /api/v1/mcp-tokens`

请求：

```json
{
  "name": "家中 Codex",
  "expires_in_days": 365
}
```

`expires_in_days` 只允许 `90`、`365` 或 `null`。成功返回 `201`，包含 Token 元数据和只出现一次的 `token`。响应必须设置 `Cache-Control: no-store`。

#### `DELETE /api/v1/mcp-tokens/{token_id}`

将当前用户所属 Token 标记为撤销并返回 `204`。已经撤销时继续返回 `204`；不存在或不属于当前用户时统一返回 `404`，接口不得暴露其他用户是否拥有该 ID。

### 内部认证 API

#### `POST /internal/mcp/v1/auth/verify`

从 Authorization Bearer Header 取得用户级 MCP Token，验证摘要、到期、撤销和账号启用状态。成功返回 `204`，不返回用户信息；所有认证失败统一返回：

```json
{
  "error": {
    "code": "authentication_required",
    "message": "需要 MCP 认证",
    "request_id": "...",
    "details": []
  }
}
```

内部任务路由复用同一认证服务，但返回解析出的 `User`，随后继续把 `user.id` 交给现有任务 service。

## 密码修改与会话安全

新增：

```text
PUT /api/v1/account/password
```

请求只包含 `current_password` 和 `new_password`。API 从 Web access token 获取当前用户，不接受用户名或 `user_id`。确认新密码只属于 Web 表单校验，服务端仍独立执行全部密码规则。

处理规则：

- 当前密码错误返回 `400 invalid_current_password`。
- 新密码继续使用现有密码长度规则和 Argon2 散列，本阶段不顺带修改密码政策。
- 新旧密码相同返回 `400 password_unchanged`。
- 更新密码、递增 `auth_version` 和撤销全部 refresh 会话必须在一个事务中完成。
- 成功响应清除 refresh Cookie；Web 同时清除内存 access token 并返回登录页。
- 密码、密码散列和第三方校验异常不得进入响应或日志。

成功返回 `204`，不返回用户或会话数据。

### Access Token 立即失效

给 `users` 增加非空整数 `auth_version`，默认值为 `1`。Web access token 增加必需的 `ver` claim；认证时重新读取用户并比较版本。

修改或由 CLI 重置密码时递增 `auth_version`，从而让所有旧 access token 立即失效，而不是继续保留最长 15 分钟的使用窗口。迁移上线前签发且缺少 `ver` 的 access token 会要求用户重新登录一次。

MCP Token 与 Web 密码是独立凭据。修改密码不自动撤销 MCP Token；账号停用则同时阻止 Web 登录、Web token 刷新和全部 MCP Token。

## Web 设置页

认证后页面使用：

```text
/           Todo 工作区
/settings   当前用户设置
```

当前只有两个认证后页面，不新增路由依赖。认证 Shell 使用一个小型 History API 导航层处理 `pushState` 和 `popstate`；Caddy 的 SPA fallback 已允许直接刷新 `/settings`。

把现有 Todo Header 提升为两个页面共享的认证 Shell：

- 品牌入口返回待办页；
- 显示当前用户名；
- 提供设置入口；
- 保留退出登录。

未知认证后路径回到 `/`。从设置页返回任务页时重新获取任务数据，避免为了两个页面引入跨页面任务缓存。

### 账号安全区

- 展示只读用户名和时区。
- 表单包含当前密码、新密码和确认新密码。
- 前端先校验确认密码一致，并处理提交中、错误和禁用状态。
- 密码修改成功后立即回到登录页，显示“密码已修改，请重新登录”。
- 账号区错误不得阻断 MCP Token 区。

### MCP Token 区

创建表单包含名称和有效期，默认 365 天。创建成功后显示一次性结果面板：

- 完整 Token；
- 一键复制；
- MCP 地址和配置示例；
- “关闭后无法再次查看”的明确提示。

关闭结果面板时立即从 React 状态清除明文。原始 Token 不进入 URL、日志、`localStorage`、`sessionStorage` 或 IndexedDB。

Token 列表展示名称、简短 Token ID、状态、创建时间、最近使用时间和到期时间。撤销前使用包含 Token 名称的确认 Dialog；成功后原位更新状态。列表、创建和撤销分别维护加载与错误状态。桌面端使用紧凑列表，移动端使用纵向卡片，所有交互支持键盘操作和可读的焦点状态。

设置页不提供注册、创建其他用户、修改用户名或管理其他账号的能力。

## CLI 多账号调整

管理员仍通过现有 CLI 创建和维护账号。`user create` 从“数据库存在任意用户就拒绝”调整为“仅拒绝规范化用户名重复”。数据库用户名唯一约束是最终并发保护，CLI 将唯一约束冲突映射为稳定且不泄露内部 SQL 的错误。

现有 `user change-password`、`user deactivate` 和 `user revoke-sessions` 继续按用户名定位账号。CLI 密码重置复用与 Web 相同的事务原语：更新密码、递增 `auth_version`、撤销全部 refresh 会话，但管理员重置不要求旧密码。

## 配置与兼容迁移

### 数据库迁移

新增一个 Alembic revision：

- 为 `users` 增加 `auth_version`，非空，服务端默认 `1`；
- 创建 `mcp_tokens` 表、外键、唯一摘要约束和必要索引；
- downgrade 先删除 `mcp_tokens`，再移除 `auth_version`。

迁移必须保留现有用户、任务和 refresh 会话。上线后旧 Web access token 因缺少 `ver` 失效，用户需要重新登录。

### 静态 MCP Token 退役

移除 API 与 MCP 的 `TICKLY_MCP_TOKEN_SHA256` 配置、校验和 Compose 必填项。MCP readiness 只检查自身生命周期和 API `/ready`；API readiness 继续负责数据库与 migration 状态。

现有静态 Token 无法安全映射到某个用户，也没有原文可供迁移，因此不提供自动转换或双认证兼容期。部署升级步骤为：

1. 部署并执行数据库迁移；
2. 管理员按现有 CLI 创建需要的额外账号；
3. 用户登录 Web 设置页创建自己的 MCP Token；
4. 用户更新 MCP 客户端环境变量并重新连接；
5. 从服务器环境删除旧静态摘要。

升级期间旧 MCP Token 会停止工作，这是一次明确的凭据轮换边界。文档不得把旧摘要误写成仍受支持。

## 错误与安全边界

- 缺失、格式错误、不存在、摘要不匹配、过期、撤销和账号停用统一表现为 `401 authentication_required`。
- Token 管理接口只能按 `CurrentUser.id` 查询和写入，不能先按全局 ID 查询后再在应用层判断所有权。
- 任何日志都不得记录 Authorization、Token、Token 摘要、密码、请求正文或含敏感字段的异常对象。
- 创建响应使用 `Cache-Control: no-store`；错误正文不得回显请求中的 Token 或密码。
- 账号停用检查在每次 Web access token 与 MCP Token 认证时执行，确保维护操作立即生效。
- MCP Token 不能访问 `/api/v1/tasks`，Web JWT 不能访问 `/internal/mcp/v1/tasks`。
- Token 撤销和到期判断使用 UTC；用户时区只影响 Web 展示。
- 撤销发生在 MCP 传输验证之后时，内部任务接口的二次验证必须阻止数据访问。

## 测试设计

### API

- migration upgrade/downgrade、已有数据保留、外键与唯一约束。
- CLI 可创建多个不同用户名账号，并发或重复用户名仍被拒绝。
- Web 密码修改验证旧密码、相同密码、密码规则、事务回滚和统一错误。
- 密码修改后旧 access token、全部 refresh 会话立即失效，新密码可登录。
- 每个用户可以创建多个 Token，并且只能列出和撤销自己的 Token。
- Token 明文只在创建响应出现一次，列表和错误不包含明文或摘要。
- 90 天、365 天、永不过期、到期、撤销和账号停用语义。
- 两个用户使用相同任务 `serial` 时仍只能访问各自任务。
- MCP Token 与 Web JWT 不能跨认证域使用。
- 撤销竞态测试：传输验证成功后撤销，任务 API 再验证时必须失败。
- `last_used_at` 条件更新不造成每次请求写入，且遥测更新失败不改变授权结果。

### MCP

- `/health`、`/ready` 继续公开，`/mcp` 缺少 Token 返回 `401`。
- MCP 中间件把原始 Token 和规范 request ID 精确透传给内部验证 API。
- 无效 Token 在协议解析前被拒绝，不暴露 MCP 协议细节。
- API 不可达、超时、非 JSON 和未知状态码映射为稳定错误，且不泄露 Token 或内部 URL。
- 七个工具都透传当前请求 Token，不缓存或复用其他请求的凭据。
- 两个并发 MCP 客户端使用不同用户 Token 时不会串号。

### Web

- 登录用户可以在任务页和设置页之间导航，刷新 `/settings` 后能恢复认证状态。
- 密码表单校验确认密码、提交状态、服务端错误和成功后退出。
- Token 列表覆盖加载、空状态、错误、过期与撤销状态。
- 创建结果只显示一次，复制成功有反馈，关闭后明文从界面移除。
- 撤销确认、重复点击禁用、成功更新和失败恢复。
- Token 不写入任何浏览器持久存储或 URL。
- 桌面和移动布局、键盘操作、焦点回归及可访问名称。

### 集成与部署

- 运行 Web lint、typecheck、build 和 Vitest。
- 运行 API 与 MCP 全量 pytest，核对真实 HTTP Bearer、`serial`、错误码和 request ID 契约。
- 校验基础 Compose 与 Traefik Compose，不展开含敏感值的配置。
- 运行两种 Compose 检查脚本并构建 API、MCP、Web 镜像。
- 使用两个真实 CLI 账号和两个 MCP Token 做本地 HTTP smoke，证明相同 `serial` 不会跨用户读取或修改。

## 验收标准

- 管理员可通过现有 CLI 创建两个或更多用户名唯一的账号，Web 没有注册入口。
- 每个账号可以修改自己的密码，并立即终止此前的 Web 会话。
- 每个账号可以创建多个具名 MCP Token，支持 90 天、365 天和永不过期。
- Token 只在创建时显示一次，可单独撤销，撤销、到期和账号停用立即阻止后续 MCP 数据访问。
- 不同用户在 MCP 中只看到和操作自己的任务，即使任务 `serial` 相同也不会串号。
- MCP 仍不直接访问 SQLite，客户端不能指定或伪造 `user_id`。
- 静态共享 MCP Token 配置和相关文档全部退役，部署文档明确新的用户级 Token 获取与轮换流程。
