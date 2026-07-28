# Tickly 阶段 3：Todo API 设计

## 1. 目标

在现有 FastAPI、JWT 认证、SQLAlchemy、SQLite 和 Alembic 基础上，交付完整且受当前用户所有权保护的 Todo 服务端能力。

本阶段完成后，已登录用户可以创建、查询、筛选、排序、分页、修改、完成、取消完成和删除自己的任务；不存在与属于其他用户的任务使用相同的 `404` 响应。Todo Web 和 AI 能力仍不实现。

## 2. 当前基础

阶段 0–2 已提供：

- FastAPI 应用工厂、`/health`、数据库与 migration 感知的 `/ready`。
- 请求 ID、统一错误结构、SQLite busy 映射和脱敏的内部错误。
- SQLAlchemy Session 请求依赖及显式事务模式。
- 用户、认证会话和任务 ORM 模型及首份 Alembic migration。
- 用户名登录、JWT access token、refresh Cookie 和 `CurrentUser` 依赖。
- API pytest、Web 测试和跨平台 Docker smoke 入口。

现有 `tasks` 表已经包含本阶段需要的字段、约束和索引，因此本阶段不创建 migration，也不修改历史 schema。

## 3. 范围

### 3.1 包含

- 创建、列表、详情、部分更新和硬删除任务。
- 完成与取消完成任务，并由服务端维护 `completed_at`。
- All、Active、Completed 状态筛选。
- 按创建时间、截止时间和优先级升序或降序排序。
- 使用不透明 cursor 的稳定 keyset 分页。
- 所有查询绑定当前 `user_id`。
- 请求、响应、查询参数和 cursor 的稳定校验。
- CRUD、筛选、排序、分页、回滚和用户隔离测试。
- OpenAPI 契约和当前事实文档更新。

### 3.2 不包含

- Todo Web 页面、客户端状态管理或 UI smoke。
- AI 草稿、自然语言解析或模型调用。
- 公开注册、第二账号 CLI 或用户管理 API。
- 软删除、回收站、审计历史、任务版本号、ETag 或乐观锁。
- 离线缓存、冲突合并、实时推送或 WebSocket。
- 标签、清单、重复任务、提醒、附件或任务共享。
- 通用 repository、通用 cursor 框架或新的 workspace 包。

## 4. 核心决策

### 4.1 分层

采用聚焦式 schema、service 和薄路由，不新增 repository 层：

```text
HTTP /api/v1/tasks
  -> CurrentUser + DbSession
  -> task request/query schema
  -> task service
  -> SQLAlchemy Task
  -> SQLite
```

新增主要文件：

- `apps/api/app/schemas/tasks.py`：创建、PATCH、响应、列表参数与枚举。
- `apps/api/app/services/tasks.py`：CRUD、所有权、事务、排序和 cursor。
- `apps/api/app/api/routes/tasks.py`：HTTP 状态码、领域错误映射与响应组装。
- `apps/api/tests/test_task_schemas.py`：字段、时间和 PATCH 语义。
- `apps/api/tests/test_tasks_service.py`：事务、状态变化、排序、分页和所有权。
- `apps/api/tests/test_tasks_api.py`：真实 HTTP 契约、认证、错误与 OpenAPI。

路由不得直接执行 SQL、提交事务、维护完成时间或解析 cursor。service 不构造 HTTP 响应。

### 4.2 认证与所有权

全部任务路由依赖现有 `CurrentUser`。详情、修改和删除必须在同一条 SQL 条件中同时使用：

```text
tasks.id = task_id AND tasks.user_id = current_user.id
```

不得先按任务 ID 查询后再单独判断所有权。任务不存在和任务属于其他用户都抛出相同领域异常，并映射为相同的 `404 task_not_found`，避免暴露其他用户任务是否存在。

路径中的 `task_id` 作为字符串进入同一所有权查询，不在 HTTP 层单独返回 UUID 格式错误。格式错误、不存在和属于其他用户的任务统一返回 `404 task_not_found`。

列表查询始终把 `Task.user_id == current_user.id` 放入数据库条件。cursor 不包含或覆盖用户 ID，服务端始终以当前认证用户为准。

### 4.3 事务

- 创建、修改和删除各自拥有一个明确事务。
- service 在成功时提交；任意校验、flush、commit 或数据库异常都回滚后继续抛出。
- 详情和列表只读，不提交事务。
- PATCH 每次成功都显式更新 `updated_at`，即使提交的值与当前值相同。
- 多设备并发修改采用最后写入生效，不增加版本检查。
- 现有请求 Session 依赖继续在未处理异常时提供第二层回滚保护。

## 5. 数据契约

### 5.1 枚举

```text
TaskPriority = none | low | medium | high
TaskStatus   = all | active | completed
TaskSort     = created_at | due_at | priority
SortOrder    = asc | desc
```

请求 schema 禁止额外字段，避免客户端写入 `id`、`user_id`、`completed_at`、`created_at` 或 `updated_at`。

### 5.2 创建请求

`POST /api/v1/tasks` 接受：

```json
{
  "title": "完成阶段 3 设计",
  "notes": "确认 cursor 和所有权规则",
  "priority": "high",
  "due_at": "2026-07-30T18:00:00+08:00"
}
```

规则：

- `title` 必填；去除首尾空白后长度为 1–200 个字符。
- `notes` 可省略或为 `null`，非 null 字符串最多 4000 个字符；保留用户输入的换行和空白，包括空字符串。
- `priority` 可省略，默认 `none`。
- `due_at` 可省略或为 `null`。非空值必须携带 `Z` 或明确的 UTC 偏移，服务端转换为 UTC。
- 创建请求不接受 `is_completed` 或 `completed_at`；新任务固定为未完成。

成功返回 `201 Created` 和完整任务响应。

### 5.3 PATCH 请求

`PATCH /api/v1/tasks/{task_id}` 可包含：

```json
{
  "title": "新的标题",
  "notes": null,
  "priority": "medium",
  "due_at": null,
  "is_completed": true
}
```

规则：

- 请求必须至少出现一个允许字段；空对象返回 `422 validation_error`。
- 使用 Pydantic 的 `model_fields_set` 区分字段未出现和显式 `null`。
- `notes: null` 清空备注，`due_at: null` 清空截止时间。
- `title`、`priority` 和 `is_completed` 显式传 `null` 返回 `422 validation_error`。
- `title`、`notes`、`priority` 和 `due_at` 使用与创建请求相同的校验。
- 客户端不能直接设置 `completed_at`。

完成状态由服务端维护：

- 未完成任务设置 `is_completed=true` 时，使用当前 UTC 时间写入 `completed_at`。
- 已完成任务再次设置 `true` 时保留原 `completed_at`，但仍更新 `updated_at`。
- 设置 `is_completed=false` 时清空 `completed_at`。
- PATCH 未出现 `is_completed` 时同时保留现有 `is_completed` 和 `completed_at`。

成功返回 `200 OK` 和更新后的完整任务响应。

### 5.4 任务响应

单任务响应不暴露 `user_id`：

```json
{
  "id": "0a0c26fd-52ad-45d9-9f7c-2b20181c4407",
  "title": "完成阶段 3 设计",
  "notes": null,
  "is_completed": false,
  "priority": "high",
  "due_at": "2026-07-30T10:00:00Z",
  "completed_at": null,
  "created_at": "2026-07-28T08:00:00Z",
  "updated_at": "2026-07-28T08:00:00Z"
}
```

SQLite 读取 `DateTime(timezone=True)` 时可能返回无 `tzinfo` 的值。响应层必须把数据库中的无时区时间解释为 UTC，并把所有非空时间序列化为带 UTC 信息的 ISO 8601 值，不向客户端返回无时区时间。

## 6. HTTP API

| 方法 | 路径 | 成功状态 | 行为 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/tasks` | `201` | 创建当前用户的未完成任务 |
| `GET` | `/api/v1/tasks` | `200` | 当前用户任务的筛选、排序和分页列表 |
| `GET` | `/api/v1/tasks/{task_id}` | `200` | 获取当前用户任务详情 |
| `PATCH` | `/api/v1/tasks/{task_id}` | `200` | 部分更新、完成或取消完成 |
| `DELETE` | `/api/v1/tasks/{task_id}` | `204` | 硬删除当前用户任务 |

删除成功响应体为空。重复删除已经不存在的任务返回 `404 task_not_found`；本阶段不保留墓碑记录。

路由使用 `APIRouter(prefix="/tasks", tags=["tasks"])`，并由现有 `/api/v1` router 纳入应用。每个 HTTP operation 使用独立函数和明确的请求、响应类型。

## 7. 列表查询

### 7.1 参数

```text
status=all|active|completed        默认 all
sort=created_at|due_at|priority   默认 created_at
order=asc|desc                    默认 desc
limit=1..100                      默认 50
cursor=<opaque value>             默认无
```

状态映射：

- `all`：不增加完成状态条件。
- `active`：`is_completed = false`。
- `completed`：`is_completed = true`。

列表响应：

```json
{
  "items": [],
  "next_cursor": null
}
```

不返回 `total`，避免每页执行额外的 count 查询。

### 7.2 排序

- `created_at` 使用任务创建时间。
- `due_at` 使用截止时间；`null` 在升序和降序中都固定排在所有非空值之后。
- `priority` 使用 `none=0`、`low=1`、`medium=2`、`high=3` 的显式 CASE 排序。
- 每种排序都追加同方向的 `id` 作为唯一决胜字段。

默认顺序为 `created_at desc, id desc`。例如 `priority desc` 表示 high、medium、low、none，同优先级内按 `id desc`。

### 7.3 keyset cursor

cursor 使用 Base64URL 编码的版本化 JSON，不使用 offset。版本 1 固定包含：

```json
{
  "v": 1,
  "status": "active",
  "sort": "due_at",
  "order": "asc",
  "null_bucket": false,
  "value": "2026-07-30T10:00:00Z",
  "id": "0a0c26fd-52ad-45d9-9f7c-2b20181c4407"
}
```

约束：

- cursor 是不透明但不签名的定位信息，不作为授权或可信数据来源。
- 解码后严格验证版本、字段集合、类型、枚举、时间和 UUID。
- cursor 内的 `status`、`sort` 和 `order` 必须与当前查询一致；`limit` 不绑定，可以在后续页调整。
- cursor 损坏、版本未知、字段无效或与查询不匹配统一返回 `422 invalid_cursor`。
- 查询读取 `limit + 1` 行；存在额外一行时，以最后一条实际返回任务生成 `next_cursor`。
- `due_at` cursor 区分非空和 null bucket。进入 null bucket 后只使用任务 ID 继续翻页。
- cursor 不包含 `user_id`；所有分页 SQL 仍强制使用当前用户条件。

keyset 分页保证排序字段未变化时不重复、不漏读同值记录。它不提供跨请求数据库快照；任务在翻页期间被新增、删除或修改排序字段时，后续页反映最新数据库状态。

## 8. 错误契约

所有错误继续使用现有统一结构：

```json
{
  "error": {
    "code": "task_not_found",
    "message": "任务不存在",
    "request_id": "request-id",
    "details": []
  }
}
```

| 场景 | HTTP | code | message |
| --- | --- | --- | --- |
| 缺少或无效 access token | `401` | `authentication_required` | `需要登录` |
| 请求体或普通查询参数无效 | `422` | `validation_error` | `请求参数无效` |
| cursor 无效或与查询不匹配 | `422` | `invalid_cursor` | `分页游标无效` |
| 任务不存在或不属于当前用户 | `404` | `task_not_found` | `任务不存在` |
| SQLite 锁竞争 | `503` | `database_busy` | `数据库繁忙，请稍后重试` |
| 其他未预期异常 | `500` | `internal_error` | `服务器内部错误` |

错误响应不得包含任务正文、其他用户 ID、SQL、数据库路径、cursor 解码细节或堆栈。Pydantic validation details 继续只返回 location、type 和 message，不回显输入值。

## 9. Service 接口

service 提供面向用例的明确函数，参数使用当前用户 ID，而不是 User ORM 对象：

```text
create_task(session, user_id, payload) -> Task
list_tasks(session, user_id, query) -> TaskPage
get_task(session, user_id, task_id) -> Task
update_task(session, user_id, task_id, payload) -> Task
delete_task(session, user_id, task_id) -> None
```

`TaskPage` 包含任务序列和可空的 `next_cursor`。领域异常只表达 `TaskNotFound` 和 `InvalidCursor`；字段与普通查询参数在 schema 层完成校验。

第一版将 task CRUD、列表和 cursor 放在同一个聚焦 service 模块中。只有实现后文件复杂度确实超过单一职责边界时，才把纯 cursor 编解码拆到任务领域内部模块；不提前创建通用 cursor 包。

## 10. 测试策略

### 10.1 Schema 测试

- title 去除首尾空白，空标题、超长标题和显式 null 被拒绝。
- notes 的 null、空字符串、换行保留和 4000 字符边界。
- priority 枚举和默认值。
- due_at 接受 `Z` 与明确偏移，拒绝无时区时间，并转换为 UTC。
- PATCH 区分未传与显式 null，拒绝空对象和额外字段。
- 数据库返回的无时区时间被响应 schema 解释为 UTC。

### 10.2 Service 测试

- 创建写入当前用户、默认状态和 UTC 时间。
- 详情、修改和删除始终绑定 `task_id + user_id`。
- 不存在与其他用户任务抛出同一 `TaskNotFound`。
- 完成、重复完成、取消完成和 `updated_at` 规则。
- 创建、修改和删除异常时事务回滚。
- All、Active、Completed 筛选。
- created_at、due_at 和 priority 的升降序。
- due_at null 固定末尾。
- 同排序值下通过 ID 稳定翻页。
- limit+1、末页无 cursor、调整 limit 后继续分页。
- cursor 损坏、版本错误、字段错误和跨查询复用。

用户隔离测试直接向迁移后的临时数据库插入第二个用户及任务。它只验证服务端所有权边界，不增加第二账号 CLI、注册接口或多账号产品能力。

### 10.3 API 测试

- 五个任务路由均要求 Bearer access token。
- CRUD 的成功状态码、响应体和 UTC 时间。
- validation、invalid_cursor、task_not_found 和 database_busy 结构稳定。
- 跨用户详情、修改和删除与不存在任务返回完全相同的 `404`。
- DELETE 返回空的 `204`。
- OpenAPI 包含全部任务 operation，并不暴露内部字段。

所有测试使用 Alembic migration 后的临时文件 SQLite 和真实 SQLAlchemy Session，不使用内存 SQLite 替代持久化行为，也不 mock 核心数据库查询。

## 11. 验证

本阶段是 API 改动，开发环境执行：

```powershell
mise exec -- pnpm test:api
```

同时执行静态范围检查：

```powershell
git diff --check
rg -n "user_id|task_not_found|invalid_cursor|completed_at" apps/api/app apps/api/tests
```

Web lint、typecheck 和 build 不属于本阶段默认验证，因为阶段 3 不修改 Web。Docker 配置和镜像路径不变化时不重复扩展 smoke；现有 smoke 继续验证 migration、账号创建和认证闭环。

## 12. 验收标准

- 已登录用户可以完成全部任务 CRUD，未登录请求返回 `401`。
- 用户只能访问自己的任务；不存在与跨用户访问返回相同 `404 task_not_found`。
- title、notes、priority、due_at、PATCH 和列表参数具有明确边界与稳定错误。
- 完成、重复完成和取消完成遵守 `completed_at` 不变量。
- 三种状态筛选和三种排序均支持升降序。
- cursor 在同值排序、null 截止时间和不同 limit 下稳定工作。
- cursor 无效或跨查询复用返回 `422 invalid_cursor`，且不泄漏解码细节。
- 写操作失败时回滚，数据库锁竞争沿用 `503 database_busy`。
- 响应不包含 `user_id` 或其他内部字段，所有时间都携带 UTC 信息。
- 全部 API 测试通过，文档只描述真实实现能力。
