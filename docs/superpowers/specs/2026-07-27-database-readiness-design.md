# Tickly 数据库 Readiness 设计

## 目标

为阶段 1 的 SQLite 与 Alembic 数据层补齐运行时 readiness 检查，使 `GET /ready` 只有在应用生命周期已启动、数据库可访问且数据库 revision 与当前 migration head 一致时才返回成功。

该设计区分进程存活与业务可用：

- `GET /health` 继续作为不访问数据库和外部服务的基础健康检查。
- API 进程不会因为数据库暂时不可用或 migration 落后而在启动阶段直接退出。
- `GET /ready` 在上述异常状态下返回 `503`，供 Docker healthcheck、部署流程和运维诊断使用。

## 当前状态

- `GET /health` 返回固定的 `{"status": "ok"}`。
- `GET /ready` 只检查应用是否进入 FastAPI lifespan，不访问数据库。
- SQLAlchemy Engine、请求级 Session factory、SQLite PRAGMA、三个 ORM 模型和首份 Alembic migration 已实现。
- migration 测试已覆盖临时文件 SQLite 的 upgrade 与 downgrade。
- 尚未实现认证、Todo API 或 Web 业务功能。

## 非目标

本次不实现：

- CLI 账号管理、密码哈希、JWT 或认证路由。
- Todo 业务路由或 Web 页面。
- 自动执行 migration，或在应用启动时调用 `create_all()`。
- readiness 结果缓存、后台轮询或独立监控系统。
- 修改 `/health` 的现有语义。

## 方案选择

采用每次 `GET /ready` 请求实时检查数据库状态的方案。

未采用的方案：

- 启动时检查并缓存结果：无法反映运行期间数据库断开或 schema 落后的状态。
- 只执行数据库连通性检查：无法阻止新版本 API 在旧 schema 上接收业务流量。

当前部署是单 VPS、单 API 进程，readiness 请求频率低。实时检查的额外开销可控，不需要提前引入缓存与失效机制。

## 组件边界

### 数据库 readiness 模块

新增独立模块负责：

1. 使用 SQLAlchemy Engine 建立连接并执行轻量查询，证明数据库可访问。
2. 通过 Alembic `MigrationContext` 读取数据库当前 revisions。
3. 通过 Alembic `ScriptDirectory` 读取代码中的 migration heads。
4. 比较两个 revision 集合并返回明确结果。

revision 比较使用 `get_current_heads()` 与 `get_heads()`，不使用只适合单分支 migration 流的 `get_current_revision()`。即使未来出现多个 migration heads，判断仍保持正确。

该模块不依赖 FastAPI Request，也不直接构造 HTTP 响应。它只表达数据库 readiness 状态，HTTP 映射由路由负责。

### FastAPI 应用工厂

`create_app()` 增加可选 SQLAlchemy Engine 注入点：

- 未注入时，根据本次应用实际使用的 `Settings.database_url` 创建 Engine。
- 测试注入临时文件 SQLite Engine，使测试不接触开发数据库。
- Engine 保存到 `application.state`，供 readiness 路由读取。
- 应用只负责释放自己创建的 Engine；调用方注入的 Engine 由调用方释放。

应用是否进入 lifespan 仍由 `application.state.ready` 表示。该状态只说明应用生命周期已启动，不代表数据库已经 ready。

### Readiness 路由

`GET /ready` 使用普通同步路由函数。SQLAlchemy 与 Alembic 检查是同步阻塞操作，FastAPI 会在线程池中执行该路由，避免阻塞事件循环。

处理顺序：

1. 检查应用是否进入 lifespan。
2. 检查数据库是否可访问。
3. 检查数据库 revisions 是否等于代码 migration heads。
4. 全部通过后返回 `200`。

`GET /health` 不读取 Engine，也不触发任何数据库连接。

## 接口行为

### 成功

状态码：`200`

```json
{
  "status": "ready"
}
```

### 应用生命周期尚未启动

状态码：`503`

错误码：`not_ready`

消息：`服务尚未就绪`

### 数据库不可访问

状态码：`503`

错误码：`database_unavailable`

消息：`数据库不可用`

包括无法建立连接、无法执行查询和底层数据库驱动错误。客户端响应不得包含数据库 URL、文件路径、SQL 或异常文本。

### Migration 不是最新

状态码：`503`

错误码：`migration_not_current`

消息：`数据库迁移版本不是最新`

空数据库、缺少 `alembic_version` 记录、revision 落后以及 revision 集合不一致均属于该状态。

所有失败响应继续使用现有统一错误结构，并包含 request ID。底层异常通过结构化日志记录，但不得记录数据库凭据或完整连接 URL。

## 数据流

```text
GET /ready
    │
    ├── lifespan 未启动 ───────────────▶ 503 not_ready
    │
    ▼
application.state.database_engine
    │
    ├── 连接或查询失败 ────────────────▶ 503 database_unavailable
    │
    ▼
MigrationContext.get_current_heads()
    │
    ├── 与 ScriptDirectory.get_heads()
    │   集合不一致 ───────────────────▶ 503 migration_not_current
    │
    ▼
200 {"status": "ready"}
```

## 异常与资源管理

- Readiness 检查使用短事务或连接上下文，响应完成前归还连接。
- 数据库异常转换为稳定的应用错误，不由通用异常处理器返回 `500`。
- migration mismatch 是预期运维状态，不记录为未处理异常。
- 应用关闭时释放由应用工厂创建的 Engine。
- 测试注入的 Engine 不由应用擅自释放，便于测试在请求后继续断言数据库状态。
- 本次不执行 migration；生产 migration 仍由独立发布步骤负责。

## 测试策略

测试使用真实临时文件 SQLite，不把内存数据库作为唯一集成环境。

### 路由行为

- 数据库不可用时，`GET /health` 仍返回 `200`。
- lifespan 外调用 `GET /ready` 返回 `503 not_ready`，且不访问数据库。
- 已执行 `alembic upgrade head` 的数据库返回 `200`。
- 空数据库返回 `503 migration_not_current`。
- 指向无法创建或打开位置的 SQLite URL 返回 `503 database_unavailable`。
- 所有错误响应保留 request ID 和统一错误结构。

### Revision 判断

- 数据库 current heads 与 script heads 相同时为 ready。
- current heads 为空或集合不相同时为 not current。
- 比较按集合执行，为多个 migration heads 保留兼容性。

### 回归检查

- 现有数据库、migration、健康检查和统一错误测试全部通过。
- Web lint、typecheck 和 build 继续通过。
- API 全量 pytest 通过。
- Compose 配置仍可解析。

## 验收标准

- API 可以在数据库不可用或 migration 落后时启动。
- `/health` 始终不依赖数据库。
- `/ready` 每次请求都实时检查数据库连通性和 Alembic revision。
- 已迁移到 head 的数据库返回 ready。
- 数据库不可用和 migration 落后分别返回稳定的 `503` 错误码。
- 客户端错误不泄漏连接信息或底层异常。
- 应用创建和释放 Engine 的所有权边界明确。
- 相关测试使用临时文件 SQLite，并覆盖成功与两类失败状态。
