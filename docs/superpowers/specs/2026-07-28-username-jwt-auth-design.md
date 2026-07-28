# Tickly 阶段 2：用户名与 JWT 认证设计

## 目标

在现有 FastAPI、SQLite、SQLAlchemy 与 Alembic 基础上，完成个人多设备应用的单账号认证闭环：管理员通过后端 CLI 创建和维护唯一账号，用户使用用户名和密码登录，Web 通过短期 access token 与可轮换 refresh Cookie 保持会话。

本阶段完成后，未认证请求不能访问后续业务 API，Web 可以在刷新页面后恢复认证状态，登出、修改密码、停用账号和安全操作能够按规则撤销会话。

## 已确认决策

- 不使用邮箱、手机号或其他外部联系方式作为账号标识。
- 登录标识为用户名，不增加独立显示名称。
- 用户名长度为 3–32 个字符，只允许英文字母、数字、下划线和连字符。
- 用户名输入先去除首尾空白并转为小写，保存值与唯一性比较均大小写不敏感。
- 当前没有历史数据，直接将首份 migration、ORM、测试和路线图中的 `email` 改为 `username`，不增加兼容 migration。
- 第一版保持单账号边界：只有 CLI 可以创建账号，数据库已有任何用户时拒绝创建第二个账号。
- 不提供公开注册、找回密码、用户名重命名、账号重新激活或账号删除能力。
- access token 使用短期 JWT Bearer；refresh token 使用可轮换 JWT，并只存入 Secure HttpOnly Cookie。
- Web 不把 access token 写入 `localStorage`、`sessionStorage`、IndexedDB 或其他持久浏览器存储。

## 范围

### 包含

- `users` schema 从邮箱登录标识调整为用户名登录标识。
- 用户名规范化与验证。
- Argon2 密码哈希和校验。
- access JWT 与 refresh JWT 的签发和严格校验。
- CLI 创建账号、修改密码、停用账号和撤销全部会话。
- 登录、刷新、登出和当前用户 API。
- refresh token 哈希存储、轮换和旧 token 重放处理。
- Web 登录页、认证状态恢复、共享刷新、受保护应用壳和退出。
- API、CLI、数据层和 Web 自动化测试。
- Docker smoke 中的 CLI 创建账号和真实登录验证。
- 路线图、README 和环境变量示例的事实同步。

### 不包含

- Todo API 或 Todo 页面。
- 公开注册、邀请、找回密码邮件或管理员后台。
- 多账号创建、账号切换、角色或权限模型。
- 显示名称、头像或个人资料编辑。
- OAuth、OIDC、Passkey、MFA 或第三方登录。
- refresh token 多代宽限窗口、跨设备会话管理页面或设备命名。
- 通用 repository 层或可替换认证供应商抽象。

## 数据模型

### `users`

`email` 字段直接替换为：

| 字段 | 约束 |
| --- | --- |
| `username` | `VARCHAR(32)`，唯一、非空 |

数据库约束同时保证：

- `length(username) BETWEEN 3 AND 32`。
- `username = lower(username)`。
- `username NOT GLOB '*[^a-z0-9_-]*'`。

API 与 CLI 在进入服务层前调用同一个规范化函数：先 `strip()`，再 `lower()`，最后校验字符和长度。数据库约束是防止脚本或未来代码绕过应用校验的最后边界。

其余 `users` 字段保持不变：`id`、`password_hash`、`timezone`、`is_active`、`created_at` 和 `updated_at`。

### `auth_sessions`

沿用现有结构：

- `id` 同时作为 refresh JWT 的 `sid`。
- `user_id` 绑定唯一账号并级联删除。
- `refresh_token_hash` 保存当前 refresh token 的 SHA-256 摘要，不保存原始 token。
- `expires_at`、`revoked_at`、`last_used_at` 和 `created_at` 使用 UTC 时间。
- `user_agent` 只保存截断后的设备描述，不记录完整请求头集合。

refresh token 本身具有高熵，使用 SHA-256 摘要和常量时间比较；用户密码使用面向低熵秘密的 Argon2，二者不混用。

### Migration 策略

由于当前没有历史数据，直接修改 `0001_initial_schema`：

- 删除 `users.email`。
- 新增 `users.username` 及上述唯一约束和 CHECK 约束。
- 更新 ORM 与所有创建 `User` 的测试。

已有本地开发数据库需要删除后重新执行 `alembic upgrade head`。应用启动仍不得调用 `create_all()` 或自动执行 migration。

## 组件边界

```text
CLI
 └─ create / change-password / deactivate / revoke-sessions
                         │
                         ▼
                 account service
                         │
Web → /api/v1/auth/* → auth service → password/JWT security
                         │
                         ▼
                users + auth_sessions
```

### 安全基础

安全模块只负责：

- 用户名规范化与验证。
- Argon2 密码哈希、真实密码校验和未知用户名 dummy hash。
- JWT 签发、解码和 claims 校验。
- refresh token SHA-256 摘要与常量时间比较。

安全模块不访问数据库，不设置 Cookie，也不决定事务提交或回滚。

### 服务层

账号服务负责 CLI 用例；认证服务负责登录、刷新、登出和当前用户用例。服务层持有显式事务边界，并返回与 HTTP 或终端表现无关的结果或领域错误。

本阶段不增加 repository 层。SQLAlchemy 查询直接保留在职责清晰的服务函数中，并使用 SQLAlchemy 2.x `select()` 和显式 `update()` 风格。

### HTTP 路由

认证路由只负责：

- 解析和验证请求。
- 注入请求级数据库 Session 和当前用户。
- 将领域错误映射为稳定状态码与错误码。
- 设置或清除 refresh Cookie。
- 返回明确类型的响应 Schema。

路由不得直接哈希密码、签发 JWT 或提交业务事务。

### CLI

CLI 使用 Python 标准库参数解析与 `getpass`，不为四个命令增加额外 CLI 框架。入口统一为：

```text
python -m app.cli user create --username <name>
python -m app.cli user change-password --username <name>
python -m app.cli user deactivate --username <name>
python -m app.cli user revoke-sessions --username <name>
```

命令规则：

- `create` 在数据库已有任何用户时返回非零退出码。
- 创建和修改密码都交互式输入两次，密码最少 12 个字符，不接受明文密码参数。
- 修改密码与停用账号在同一事务中撤销该用户全部会话。
- 停用账号要求再次交互式输入完整用户名确认。
- 错误输出不包含密码、哈希、token、Cookie、数据库 URL、SQL 或堆栈。

## 配置

认证配置继续使用 `TICKLY_` 环境变量前缀，并至少包含：

- JWT signing secret。
- 固定 JWT algorithm。
- issuer 与 audience。
- access token 有效期，默认 15 分钟。
- refresh token 有效期，默认 30 天。
- refresh Cookie 名称。
- refresh Cookie 是否启用 `Secure`。

生产环境必须提供足够强度的 signing secret，缺失或仍为开发示例值时应用启动失败。开发和测试使用显式注入的隔离配置，不读取开发者本机 secret。

算法由服务端配置固定，解码时只允许这一种算法，不读取 token header 后动态选择。第一版使用单一对称签名密钥，不提前实现密钥轮换或 JWKS。

## JWT 与 Cookie 契约

### Access token

- 有效期默认 15 分钟。
- 响应中返回，Web 只保存于内存。
- claims 至少包含 `sub`、`jti`、`type=access`、`iss`、`aud`、`iat` 和 `exp`。
- `sub` 使用不可变的用户 UUID，不使用可修改的用户名。

登录与刷新响应：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 900
}
```

### Refresh token

- 有效期默认 30 天。
- 会话的绝对过期时间在登录时确定；refresh 轮换沿用同一个 `expires_at`，不会把会话无限续期。
- claims 在 access token 基础上增加 `sid`，并使用 `type=refresh`。
- 只写入 HttpOnly Cookie，不出现在 JSON 响应中。
- Cookie 使用 `SameSite=Strict`，路径为 `/api/v1/auth`。
- 生产环境强制 `Secure=true`；本地 HTTP 开发环境允许显式设为 false。
- 清除 Cookie 时使用与设置时相同的名称、路径、SameSite 和 Secure 属性。

## API 契约

统一前缀为 `/api/v1/auth`。

### `POST /login`

请求：

```json
{
  "username": "potato",
  "password": "<password>"
}
```

流程：

1. 规范化用户名。
2. 查询用户；未知用户名执行 dummy password hash。
3. 使用相同失败响应处理未知用户名、错误密码和停用账号。
4. 创建新的 `auth_sessions` 记录。
5. 签发 access token 和 refresh token。
6. 提交事务后设置 refresh Cookie 并返回 access token。

### `POST /refresh`

请求体为空，refresh token 来自 Cookie。

流程：

1. 严格解码 JWT 并校验 `sid`、`sub`、类型、签名、issuer、audience 和时间 claims。
2. 在事务内查询未撤销且未过期的会话及活动用户。
3. 常量时间比较数据库摘要与当前 token 摘要。
4. 匹配时生成新 refresh token，原子更新摘要和 `last_used_at`，并返回新 access token。
5. `sid` 存在但摘要不匹配时视为旧 token 重放，撤销对应会话并清除 Cookie。

同一 Web 页面内的并发刷新由客户端共享单个 refresh Promise，避免正常请求在同一页面内触发重放保护。不同浏览器进程或标签页真正并发使用同一个旧 token 时，安全策略仍以撤销会话为准，不增加宽限窗口。

### `POST /logout`

- refresh Cookie 有效时撤销当前设备会话。
- Cookie 缺失、过期或会话已撤销时仍保持幂等。
- 始终清除 refresh Cookie。
- 成功返回 `204 No Content`。

### `GET /me`

- 需要有效 access token。
- 返回 `id`、`username`、`timezone` 和 `is_active`。
- 不返回密码哈希、会话、JWT claims 或内部时间字段。

## 错误契约

沿用现有统一错误结构与 request ID：

| 场景 | 状态码 | code |
| --- | --- | --- |
| 未知用户名、错误密码或停用账号登录 | `401` | `invalid_credentials` |
| access token 缺失、过期或无效 | `401` | `authentication_required` |
| refresh Cookie 缺失、过期或无效 | `401` | `refresh_required` |
| 旧 refresh token 重放 | `401` | `refresh_replayed` |
| CLI 用户名或密码不符合规则 | 非零退出码 | 终端安全提示 |
| CLI 试图创建第二个账号 | 非零退出码 | 终端安全提示 |

HTTP 错误响应不回显密码、JWT、Cookie、哈希或原始数据库异常。登录失败日志不记录用户名和密码，只记录稳定事件名、request ID 和失败类别。

## 会话撤销规则

- 普通登出只撤销当前 `sid`。
- 修改密码在密码哈希更新的同一事务内撤销该用户全部会话。
- 停用账号在 `is_active=false` 的同一事务内撤销该用户全部会话。
- CLI `revoke-sessions` 撤销该用户全部未撤销会话。
- refresh token 重放只撤销对应 `sid`。
- access token 不保存于数据库，因此撤销后已签发 access token 最长仍可使用到 15 分钟过期；停用账号相关的当前用户依赖需要重新读取用户状态，使停用立即阻止业务请求。

## Web 设计

### 状态模型

Web 使用单一认证状态容器：

- `initializing`：首次加载，正在尝试 refresh。
- `anonymous`：没有可恢复的认证。
- `authenticated`：内存中存在 access token 和当前用户。

不增加持久状态库。认证容器负责内存 token、当前用户、共享 refresh Promise、登录和登出方法；纯展示组件不直接操作 token。

### 页面与交互

- 未认证时显示登录页，字段为用户名和密码。
- 登录提交期间禁用表单，支持 Enter 提交，并使用统一错误提示。
- 登录成功后显示受保护的应用壳、当前用户名和退出入口。
- Todo 页面仍不实现。
- 首次加载时先尝试 refresh；完成前显示初始化状态，避免登录页闪烁。
- 登出后立即清除内存认证状态并显示登录页。

当前只有登录页和受保护应用壳，不新增客户端路由依赖；根组件根据认证状态切换内容。出现多个真实页面后再评估路由库。

### API 请求与自动刷新

- API client 为请求添加内存中的 Bearer access token。
- 业务请求收到 `401 authentication_required` 时加入同一个 refresh Promise。
- refresh 成功后原请求只重试一次。
- refresh 失败或重试后仍为 `401` 时清除认证状态并显示登录页。
- login、refresh 和 logout 请求不进入自动刷新循环。
- access token 不写入日志、错误消息或浏览器持久存储。

## 事务与并发

- 每个 HTTP 请求使用独立 SQLAlchemy Session。
- 登录创建会话、refresh 轮换、修改密码并撤销会话、停用账号并撤销会话分别使用单一明确事务。
- refresh 轮换使用条件更新或等价的原子校验，确保旧摘要不能被两个请求同时成功消费。
- 任意数据库异常都回滚当前事务，不能设置与数据库状态不一致的新 refresh Cookie。
- SQLite busy timeout 继续沿用阶段 1 配置，锁等待超时映射到稳定的 `503 database_busy`。

## 测试策略

### 数据与安全单元测试

- 用户名规范化、长度、字符范围、小写和唯一约束。
- 首份 migration 只包含 `username`，不包含 `email`。
- Argon2 哈希与校验，日志和异常不包含密码或哈希。
- 未知用户名路径执行 dummy hash。
- access 与 refresh JWT 的签名、过期、issuer、audience、类型、固定算法、`sid` 和 `jti`。
- refresh token 摘要和常量时间比较。

### CLI 集成测试

- 从空文件数据库创建唯一账号。
- 拒绝第二个账号。
- 用户名和密码校验失败使用非零退出码。
- 密码二次输入不一致时不写入数据库。
- 修改密码后旧密码失效且全部会话撤销。
- 停用账号后登录和刷新均失败。
- 撤销会话命令不修改密码或账号状态。

测试替换 `getpass` 输入并使用临时文件 SQLite，不在测试命令中传递真实明文密码。

### API 集成测试

- 登录成功、未知用户名、错误密码和停用账号。
- `/me` 的成功与所有 access token 失败类型。
- refresh 成功轮换、旧 token 重放、过期、错误类型和已撤销会话。
- logout 幂等、会话撤销和 Cookie 清除。
- Cookie 的 HttpOnly、Secure、SameSite、Path 和有效期。
- 事务失败时会话、token 摘要和 Cookie 状态不发生部分更新。
- 错误响应与日志不泄漏用户名、密码、JWT、Cookie、哈希或数据库详情。

### Web 测试

建立 Vitest 4、jsdom 和测试 setup，并覆盖：

- 登录表单成功、失败、提交禁用和键盘提交。
- 首次加载 refresh 成功与失败。
- 多个请求共享一次 refresh。
- refresh 成功后原请求只重试一次。
- refresh 失败或认证再次失败后回到登录页。
- 登出清除内存状态。
- `localStorage` 与 `sessionStorage` 中不存在 access token。

### Docker smoke

在现有跨平台 smoke 中增加：

1. 空 volume migration。
2. CLI 创建唯一账号。
3. 通过 Caddy 单一入口调用登录 API。
4. 使用 access token调用 `/me`。
5. 确认 refresh Cookie 已设置。

smoke 使用固定测试凭据且只存在于临时容器和测试 volume；结束时继续删除容器、网络和 volume。

## 实施顺序

1. 修改用户名 schema、首份 migration 和数据层测试。
2. 实现安全配置、用户名规则、密码与 JWT 基础能力。
3. 实现账号服务和 CLI。
4. 实现认证服务、依赖、Schema 与 HTTP 路由。
5. 建立 Web 测试环境和认证状态容器。
6. 实现登录页、自动刷新和受保护应用壳。
7. 扩展 Docker smoke，更新路线图、README 和环境示例。
8. 运行 API、Web、migration 与 Docker 全部验收。

每一步都先增加失败测试或明确复现，再完成最小实现；不得在本阶段顺带创建 Todo API、通用权限框架或 AI 相关代码。

## 验收标准

- CLI 可以在空数据库创建唯一用户名账号，拒绝第二个账号。
- 用户名规则在 CLI、API、ORM 和数据库约束中一致。
- 错误用户名、错误密码和停用账号登录返回相同响应。
- 未认证请求不能访问 `/auth/me` 或后续受保护依赖。
- access token 过期后 Web 可以通过 refresh Cookie 自动恢复。
- refresh token 每次成功使用后轮换，旧 token 重放会撤销对应会话。
- 登出、修改密码、停用账号和撤销命令按设计撤销会话。
- 浏览器持久存储中不存在 access token。
- 页面刷新后认证状态可以恢复，refresh 失败后回到登录页。
- Web、API、migration 与 Docker smoke 全部通过。
