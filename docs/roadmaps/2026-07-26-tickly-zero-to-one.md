# Tickly 0→1 产品与工程路线图

## 目标

将已具备工程基线、持久化和安全登录的 Tickly，逐步建设为可部署在单台 VPS、支持个人多设备使用、具备持久化 Todo 管理和自然语言任务草稿能力的完整应用。

每个阶段都必须形成可独立验证的交付物。不得一次性铺设全部架构，也不得在核心 Todo 闭环稳定前扩展通用 AI 对话、协作或离线同步能力。

## 已确认的产品边界

- 产品形态：个人多设备 Web 应用。
- 部署方式：单台 VPS + Docker Compose。
- Web：React 19、TypeScript、Vite、Tailwind CSS 4、shadcn 与 Base UI。
- API：FastAPI 与 Python 3.13。
- 数据库：SQLite3。
- ORM：SQLAlchemy 2.x。
- schema migration：Alembic。
- 账号创建：仅通过后端 CLI，不提供公开注册接口或注册页面。
- 登录：用户名、密码与 JWT Bearer access token。
- 首项 AI 能力：自然语言生成结构化任务草稿，用户确认后才创建任务。

## 当前仓库状态

```text
tickly/
├── apps/
│   ├── web/                 # React 登录、认证状态与受保护壳层
│   └── api/                 # FastAPI、SQLite、CLI 与 JWT 认证
├── packages/                # 共享包预留目录
├── docs/
├── compose.yaml
├── package.json
├── pnpm-workspace.yaml
└── mise.toml
```

当前尚未实现：

- Todo 业务 API。
- Todo 页面与业务交互。
- AI 供应商集成。
- VPS HTTPS、备份、恢复和完整发布流程。

阶段 0 的工程与 Docker 骨架、阶段 1 的 SQLAlchemy/SQLite/Alembic 数据层和阶段 2 的用户名 + JWT 认证均已实现。Web 与 API 自动化测试已接入，Docker smoke 覆盖 migration、CLI 创建账号、登录 Cookie 与 `/me`；后续从阶段 3 Todo API 开始。

## 架构方案比较

### 方案 A：模块化单体 + SQLAlchemy（采用）

FastAPI 作为单个可部署服务，内部按认证、任务、AI、数据库和配置拆分模块。SQLAlchemy ORM 模型与 Pydantic API Schema 分离，Alembic 负责所有 schema 演进。

优点：

- 适合当前单人产品和单 VPS 部署。
- 事务、认证和数据所有权边界清楚。
- SQLite 迁移到 PostgreSQL 时可保留主要服务边界。
- Alembic 与 SQLAlchemy 的生产实践成熟。

代价：

- 比把所有内容写入单文件有更多显式结构。
- ORM 模型、API Schema 和 migration 需要分别维护。

### 方案 B：SQLModel 快速开发

用 SQLModel 同时表达部分 ORM 和 API 类型。

优点是初期代码较少；缺点是持久化模型与外部接口容易耦合，随着认证、migration 和 AI Schema 增长，边界会变得模糊。因此不采用。

### 方案 C：Local-first 与离线同步

浏览器保留本地数据库并与服务端同步。

该方案能提供更强的离线体验，但会立即引入冲突合并、同步协议、客户端迁移和多副本一致性，远超第一版需求。因此不采用。

## 总体架构

```text
Browser
  │ HTTPS
  ▼
Caddy container
  ├── static React Web
  └── /api/* reverse proxy
              │
              ▼
       FastAPI container
         ├── /api/v1/auth
         ├── /api/v1/tasks
         ├── /api/v1/ai
         ├── services
         ├── schemas
         ├── ORM models
         └── core
              │
              ▼
       /data/tickly.db
       Docker named volume
```

采用同源部署。浏览器只访问一个 HTTPS origin，Caddy 提供静态 Web、TLS 和 API 反向代理。FastAPI 不直接暴露公网端口。

API 保持单容器、单应用进程，避免多个实例同时写入 SQLite。未来需要多 API 副本或持续高并发写入时，再将数据库迁移到 PostgreSQL。

### API 内部边界

```text
routes
  │ request parsing + dependencies
  ▼
services
  │ use cases + transaction decisions
  ▼
SQLAlchemy Session
  │
  ▼
ORM models + SQLite
```

- `routes` 只负责 HTTP 契约、依赖注入和状态码。
- `services` 负责认证、任务和 AI 用例，以及显式事务边界。
- 第一版不增加通用 repository 层；只有出现可替换数据源或重复查询协议后再引入。
- ORM 模型不直接作为 API 响应，外部请求和响应由 Pydantic Schema 表达。
- 每个请求通过 FastAPI `yield` 依赖获得独立 SQLAlchemy Session。

## 分阶段路线图

### 阶段 0：工程基线与 Docker 骨架

目标：建立可持续开发、测试和容器化的共同基础。

范围：

- 修复现有 Web lint 错误。
- 将 FastAPI 整理为应用工厂与 `/api/v1` 路由结构。
- 保留 `GET /health`，增加 readiness 端点的结构，但在数据库接入前只验证应用配置。
- 建立类型化配置，区分开发、测试和生产。
- 建立统一错误响应、结构化日志和 request ID。
- Web 开发服务器将 `/api` 代理到本地 API。
- 创建 Web 多阶段 Dockerfile。
- 创建 API Dockerfile。
- 增加 `.dockerignore`，排除 Git、依赖目录、虚拟环境、缓存、构建产物和密钥。
- 容器使用非 root 用户运行。
- 建立最小 Docker Compose 冒烟验证。

验收：

- Web lint、typecheck、build 全部通过。
- API pytest 全部通过。
- `/health` 和 `/api/v1` 路由正常。
- 测试使用隔离配置。
- Web 与 API 镜像可以构建，容器可以启动并通过 healthcheck。

### 阶段 1：SQLAlchemy、SQLite 与 Alembic

目标：建立可靠且可演进的数据层。

范围：

- 接入 SQLAlchemy 2.x 与 Alembic。
- 使用类型化声明式 ORM 模型和 SQLAlchemy 2.x `select()` 风格。
- 配置 Engine、Session factory 与请求级 Session 依赖。
- SQLite 启用 foreign keys、WAL 和 busy timeout。
- 建立 `users`、`auth_sessions` 和 `tasks` ORM 模型。
- 生成并审查第一份 migration。
- readiness 检查数据库可访问，并确认当前 migration revision 为最新版本。
- 测试使用临时文件 SQLite，启用与生产相同的 PRAGMA。

验收：

- 空数据库可通过 `alembic upgrade head` 完整创建。
- migration 可升级和回退。
- ORM 约束、外键、级联与事务回滚有测试。
- API 重启后数据保持存在。
- 应用不会在启动时调用 `create_all()` 修改生产 schema。

### 阶段 2：CLI 账号与 JWT 认证

目标：完成个人多设备登录闭环。

状态：已完成。

范围：

- CLI 创建账号、修改密码、停用账号和撤销全部会话。
- 用户名规范化并建立唯一约束；不使用邮箱或显示名称。
- 使用 Argon2 哈希密码。
- 实现登录、刷新、登出和当前用户 API。
- access token 为短期 JWT Bearer。
- refresh token 为可轮换 JWT，存入 Secure HttpOnly Cookie。
- 数据库只保存 refresh token 哈希与会话状态。
- Web 增加登录页、认证状态、自动刷新和受保护页面。

验收：

- 未登录请求不能访问业务 API。
- 错误用户名和错误密码返回相同信息。
- access token 过期后可通过 refresh token 自动恢复。
- refresh token 每次使用后轮换。
- 旧 refresh token 重放会撤销对应设备会话。
- 登出、停用账号和修改密码能按设计撤销会话。
- 浏览器持久存储中不存在 access token。

### 阶段 3：Todo API

目标：建立完整、受用户所有权保护的 Todo 服务端能力。

范围：

- 创建、列表、详情、修改、完成和删除任务。
- 支持 All、Active 和 Completed 筛选。
- 支持按创建时间、截止时间和优先级排序。
- 使用稳定 cursor 分页。
- 所有查询绑定当前 `user_id`。
- 建立请求校验、统一错误码和事务测试。

验收：

- 用户只能访问自己的任务。
- 不存在和不属于当前用户的任务统一返回 `404`。
- 无效标题、时间、优先级、cursor 和状态返回稳定错误。
- CRUD、筛选、排序、分页、回滚与用户隔离全部有 API 测试。

### 阶段 4：Todo Web

目标：交付可在桌面和手机上日常使用的 Todo 产品。

范围：

- 登录后的任务首页。
- 快速新增任务。
- 任务列表、筛选与排序。
- 标题和备注编辑。
- 完成与取消完成。
- 删除确认。
- 截止时间、优先级与用户时区。
- 加载、空数据、错误、重试和禁用状态。
- 完成切换使用乐观更新，失败时回滚。
- 增加数据导出入口。
- 建立 Vitest 4、jsdom 和 Web 测试 setup。

验收：

- 桌面和手机浏览器都能完成核心操作。
- 页面刷新和更换设备后看到相同服务端数据。
- Web 测试覆盖认证状态、列表状态、CRUD 交互和乐观回滚。
- 大量任务通过分页加载，不一次返回全部数据。
- 用户可以导出完整任务数据。

### 阶段 5：自然语言任务草稿

目标：将自然语言安全转换为可审查的结构化任务。

范围：

- 实现 `POST /api/v1/ai/task-drafts`。
- 输入只包含用户文本、当前时间和用户时区。
- 服务端调用一个已配置的模型供应商。
- 通过严格 Schema 限制模型输出字段和类型。
- Web 显示可编辑草稿。
- 用户确认后调用普通 `POST /tasks` 创建任务。
- 增加超时、限流、供应商失败和无效输出处理。
- 测试使用伪模型实现，不访问真实供应商。

验收：

- “明天下午三点提醒我交报告，优先级高”能生成正确时区、截止时间和优先级草稿。
- 未经用户确认不会写入任务表。
- AI 失败不影响普通 Todo 功能。
- 默认不持久化原始 prompt 和模型响应。
- 模型密钥只存在于 API 服务端。

### 阶段 6：VPS Docker Compose、备份与恢复

目标：形成可部署、可升级、可备份和可恢复的个人线上应用。

范围：

- 生产 Compose 包含 `web`、`api` 和一次性 `migrate` 服务。
- `web` 使用 Caddy 提供静态 Web、自动 HTTPS 和 `/api` 反向代理。
- `api` 使用单副本、单应用进程与 `restart: unless-stopped`。
- `tickly-data` named volume 挂载到 `/data`。
- SQLite 数据库路径固定为 `/data/tickly.db`。
- 备份输出写入 VPS 的受限备份目录。
- migration 在发布前显式运行。
- 增加登录、刷新和 AI 限流。
- 增加健康检查、readiness、结构化日志和基础错误监控。
- 建立 CI，运行 Web、API、migration、镜像构建和 Compose 冒烟检查。

发布顺序：

```text
拉取或构建镜像
      ↓
使用 SQLite 在线备份机制备份现有数据库
      ↓
运行 migrate 一次性任务
      ↓
启动或更新单副本 API
      ↓
等待 readiness 通过
      ↓
更新 Web/Caddy
      ↓
执行登录与 Todo 冒烟测试
```

验收：

- 全新 VPS 可通过文档从零部署。
- API 不直接暴露公网端口。
- 容器重启后账号、会话和任务数据保持存在。
- migration 失败会停止发布。
- 可以从备份在全新 volume 中恢复。
- 发布保留上一版本镜像；数据库 migration 的可逆性在每次发布前单独评估。

## 数据模型

### `users`

| 字段 | 约束 |
| --- | --- |
| `id` | UUID，主键 |
| `username` | 规范化用户名，3–32 位、唯一、非空 |
| `password_hash` | Argon2 哈希，非空 |
| `timezone` | IANA 时区，默认 `Asia/Shanghai` |
| `is_active` | 布尔值，默认 true |
| `created_at` | UTC 时间，非空 |
| `updated_at` | UTC 时间，非空 |

第一版只有 CLI 可以创建用户。CLI 交互式读取密码，不接受命令参数中的明文密码。
创建命令发现数据库中已经存在用户时必须拒绝继续，确保 0→1 阶段保持单账号产品边界。

### `auth_sessions`

| 字段 | 约束 |
| --- | --- |
| `id` | UUID，主键，同时作为 refresh JWT 的 `sid` |
| `user_id` | 外键至 `users.id`，级联删除 |
| `refresh_token_hash` | 当前 refresh token 哈希，唯一、非空 |
| `expires_at` | UTC 时间，非空 |
| `revoked_at` | UTC 时间，可空 |
| `last_used_at` | UTC 时间，非空 |
| `user_agent` | 截断后的设备描述，可空 |
| `created_at` | UTC 时间，非空 |

刷新时按 `sid` 查询会话并校验 token 哈希。成功后更新哈希和 `last_used_at`；如果 `sid` 存在但哈希不匹配，则视为旧 token 重放并撤销该会话。

### `tasks`

| 字段 | 约束 |
| --- | --- |
| `id` | UUID，主键 |
| `user_id` | 外键至 `users.id`，级联删除 |
| `title` | 1–200 字符，非空 |
| `notes` | 最多 4000 字符，可空 |
| `is_completed` | 布尔值，默认 false |
| `priority` | `none`、`low`、`medium`、`high` |
| `due_at` | UTC 时间，可空 |
| `completed_at` | UTC 时间，可空 |
| `created_at` | UTC 时间，非空 |
| `updated_at` | UTC 时间，非空 |

索引：

- `(user_id, is_completed)`
- `(user_id, due_at)`
- `(user_id, created_at)`

第一版多设备同时修改采用最后写入生效，并在 Web 显示 `updated_at`。不实现离线冲突合并。

AI 草稿不建表。草稿只存在于当前响应和浏览器内存中。

## API 契约

统一前缀：`/api/v1`

### 认证

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `POST` | `/auth/login` | 校验用户名和密码，返回 access token 并设置 refresh Cookie |
| `POST` | `/auth/refresh` | 轮换 refresh token 并返回新 access token |
| `POST` | `/auth/logout` | 撤销当前设备会话并清除 Cookie |
| `GET` | `/auth/me` | 返回当前用户信息 |

access token 有效期 15 分钟。refresh token 有效期 30 天。

access JWT 至少包含：

- `sub`
- `jti`
- `type=access`
- `iss`
- `aud`
- `iat`
- `exp`

refresh JWT 额外包含 `sid`，并使用 `type=refresh`。解码时固定允许的算法，不接受 token 自带算法选择。

### 任务

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `GET` | `/tasks` | 按当前用户筛选、排序与分页 |
| `POST` | `/tasks` | 创建任务 |
| `GET` | `/tasks/{task_id}` | 获取任务详情 |
| `PATCH` | `/tasks/{task_id}` | 部分更新任务 |
| `DELETE` | `/tasks/{task_id}` | 删除任务 |

任务列表支持：

- `status=all|active|completed`
- `sort=created_at|due_at|priority`
- `order=asc|desc`
- `cursor=<opaque value>`
- `limit`，默认 50，最大 100

任务查询必须在同一个 SQL 条件中使用 `task_id` 和当前 `user_id`，不能先按任务 ID 查询后再单独判断所有权。

### AI

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `POST` | `/ai/task-drafts` | 将自然语言转换为结构化任务草稿 |

请求包含：

- `text`
- `now`
- `timezone`

响应只允许：

- `title`
- `notes`
- `priority`
- `due_at`

该接口不接受或返回 `user_id`，也不写入数据库。

### 统一错误结构

```json
{
  "error": {
    "code": "task_not_found",
    "message": "任务不存在",
    "request_id": "req_xxx",
    "details": {}
  }
}
```

状态码：

- 未认证：`401`
- 已认证但目标不存在或不属于当前用户：`404`
- 请求校验失败：`422`
- 登录或 AI 限流：`429`
- SQLite 超过 busy timeout：`503`
- AI 供应商失败：`502`
- AI 超时：`504`

## SQLite 规则

- 使用文件数据库，不使用内存数据库作为集成测试的唯一验证。
- 每个数据库连接启用 `PRAGMA foreign_keys=ON`。
- 生产数据库启用 WAL。
- 配置 busy timeout，超时后返回可重试的稳定错误。
- 所有 schema 变化由 Alembic 管理。
- 数据库文件只由一个 API 容器挂载为可写。
- 不在多个 VPS 或多个 API 副本之间共享 SQLite 文件。
- 达到多副本部署或持续高并发写入需求时，迁移到 PostgreSQL。

## Docker 与 VPS 规则

### 服务

- `web`：Caddy + 构建后的静态 Web。
- `api`：FastAPI 生产镜像。
- `migrate`：复用 API 镜像，运行 `alembic upgrade head` 后退出。

### 持久化

- `tickly-data` named volume 挂载到 `/data`。
- `/data/tickly.db` 是生产数据库唯一来源。
- 备份输出写入 VPS 上权限受限的目录，不写回同一个数据库文件。
- 备份使用 SQLite 在线备份机制，不能直接复制正在写入的数据库主文件。

### 密钥

- JWT signing secret、AI API key 和生产配置不进入镜像或 Git。
- 通过 VPS 上权限受限的环境文件或只读 secret 文件注入。
- `.env.example` 只包含变量名和非敏感示例。

### 运行

- 容器以非 root 用户运行。
- API 单副本、单应用进程。
- API 不映射公网端口，只暴露给 Compose 内部网络。
- Caddy 是唯一公网入口。
- healthcheck 证明进程可服务。
- readiness 额外证明数据库可访问且 migration revision 为最新。

## 安全策略

### 密码

- 最少 12 个字符。
- 不设置固定的大写、小写、数字、符号组合规则。
- 使用 Argon2 推荐参数生成哈希。
- 登录未知用户名时执行 dummy hash，再返回统一错误。
- 日志不得记录明文密码或密码哈希。

### Token

- access token 只保存在 Web 内存中。
- refresh token 只存在于 Secure HttpOnly Cookie 和服务端哈希中。
- Cookie 使用 `SameSite=Strict`，并限制到认证路径。
- 生产环境同源部署，不启用跨域 CORS。
- 开发环境只允许明确的本地 Web origin。
- 密码修改、账号停用和安全操作可以撤销全部会话。

### 数据与日志

- 所有业务数据访问绑定当前用户。
- 数据库与备份文件只有容器运行用户和 VPS 管理员可读。
- 日志不得记录完整 JWT、Cookie、AI 密钥或完整任务正文。
- 错误响应不得暴露 SQL、堆栈或供应商原始响应。

### AI

- 只发送生成草稿所需的最少数据。
- 模型输出始终作为不可信输入验证。
- 默认不记录或持久化原始 prompt 与响应。
- AI 接口与登录接口分别限流。
- AI 失败与普通 Todo 功能隔离。

## 错误与恢复策略

- 请求校验错误转换为统一错误结构。
- 服务层发生数据库异常时回滚当前 Session。
- SQLite 锁等待超过 busy timeout 后返回 `503 database_busy`。
- AI 限流、供应商错误和超时分别映射到稳定错误码。
- 每个错误响应包含 request ID。
- Alembic migration 失败时不启动新版本 API。
- 发布前先做在线备份，再运行 migration。
- 每次发布保留上一版本镜像，但数据库 migration 是否可回退需要逐次审查。

## 测试策略

### API 单元测试

- 密码哈希与校验。
- JWT 签发、过期、签名、受众、类型和算法错误。
- refresh token 轮换与重放。
- 时区与截止时间转换。
- AI 输出 Schema 校验。

### API 集成测试

- 使用临时文件 SQLite 和生产一致的 PRAGMA。
- 从空数据库运行全部 migration。
- CLI 创建账号，并拒绝创建第二个账号。
- 登录、刷新、登出和当前用户。
- 任务 CRUD、筛选、排序和分页。
- 用户数据隔离。
- 事务回滚与数据库锁超时。

### Web 测试

- Vitest 4 与 jsdom。
- 登录成功与失败。
- access token 自动刷新。
- 认证失效后返回登录页。
- 列表加载、空数据、错误和重试。
- 新建、编辑、完成、删除和乐观更新回滚。
- AI 草稿预览、编辑、确认与失败。

### Docker 集成测试

- 空 volume migration。
- CLI 创建账号与登录。
- 容器重启后的数据持久化。
- API 端口不直接对公网发布。
- healthcheck 与 readiness。
- migration 失败时停止部署。

### 浏览器端到端测试

1. 登录。
2. 创建任务。
3. 修改并完成任务。
4. 刷新页面确认数据存在。
5. 第二设备登录并看到相同任务。
6. 自然语言生成任务草稿。
7. 用户确认后任务才创建。
8. 登出后受保护页面不可访问。

## 0→1 完成标准

- 全新 VPS 可以按文档通过 Docker Compose 从零部署。
- 可以通过 CLI 创建唯一账号。
- 桌面和手机浏览器都能登录。
- 用户可以创建、编辑、完成、筛选和删除任务。
- 容器更新或重启后数据保持完整。
- access token 可以自动刷新。
- 登出、账号停用和密码修改可以撤销会话。
- 自然语言可以生成任务草稿，确认后才创建任务。
- SQLite 可以在线备份并在全新 volume 中恢复。
- Web、API、migration、Docker 和浏览器端到端检查全部通过。
- 整个生产系统只有一个 API 副本，SQLite 数据使用持久卷。

## 非目标

0→1 阶段不实现：

- 公开注册、邀请注册或找回密码邮件。
- 团队、任务共享、角色或权限后台。
- 标签、项目、子任务、附件或评论。
- 离线编辑、客户端数据库或同步冲突合并。
- 多 API 副本。
- PostgreSQL。
- 通用 AI 聊天、长期记忆、Agent 自动执行或流式对话。
- 计费、订阅或运营后台。

## 后续实施拆分

整个路线图不使用一份实施计划一次性执行。按以下七个独立规格与计划推进：

1. 工程基线与 Docker 骨架。
2. SQLAlchemy、SQLite 与 Alembic。
3. CLI 账号与 JWT 认证。
4. Todo API。
5. Todo Web。
6. AI 任务草稿。
7. VPS Docker Compose、备份与恢复。

阶段 0–2 已完成。下一份规格只覆盖第 4 项 Todo API；每个阶段完成、验证并审阅后，再设计下一阶段。

## 文档依据

- [FastAPI OAuth2 与 JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [FastAPI 数据库 Session 依赖](https://fastapi.tiangolo.com/tutorial/sql-databases/)
- [SQLAlchemy 2.0 文档](https://docs.sqlalchemy.org/en/20/)
- [SQLAlchemy SQLite 方言](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
- [Alembic](https://alembic.sqlalchemy.org/)
- [Docker Compose 文档](https://docs.docker.com/compose/)
- [Vitest 4 配置](https://vitest.dev/config/)
