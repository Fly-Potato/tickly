# Tickly 数据层基础设计

## 目标

为阶段 2 认证和阶段 3 Todo API 提供可演进、可测试的 SQLite 数据层。本阶段只交付数据库连接、事务边界、ORM 模型和第一份 Alembic migration，不实现业务路由或认证流程。

## 方案

在 `apps/api/app/db` 中建立同步 SQLAlchemy 2.x Engine、Session factory 和 FastAPI `yield` Session 依赖。SQLite 连接启用 `foreign_keys=ON`、WAL 和 busy timeout；测试使用临时文件数据库，并验证相同约束行为。数据库 URL 由 `Settings` 提供，默认使用 `sqlite:///./data/tickly.db`，生产环境通过 `TICKLY_DATABASE_URL` 覆盖为 `/data/tickly.db`。

ORM 使用 `DeclarativeBase`、`Mapped` 和 `mapped_column`。模型放在 `app/models`，以 UUID 字符串作为 SQLite 兼容的主键表示，UTC 时间由应用生成。`users`、`auth_sessions`、`tasks` 在首个 migration 中一起创建，外键使用级联删除，任务建立用户范围查询所需的复合索引。

Alembic 使用同步 migration，`env.py` 从应用 Settings 读取数据库 URL，并将 `Base.metadata` 作为 `target_metadata`。生产或测试数据库均通过 `alembic upgrade head` 建表；应用启动不调用 `create_all()`。

## 测试边界

- Engine 测试验证 SQLite PRAGMA、Session 提交和异常回滚。
- 模型测试验证唯一约束、非空约束、外键级联、任务索引和默认值。
- migration 测试在临时文件数据库中执行 upgrade 到 head，检查三张表和当前 revision，再 downgrade 到 base。
- 既有 FastAPI 测试继续使用显式 `Settings(_env_file=None)`，不连接真实生产数据库。

## 非目标

- 不增加 repository 层、业务 service、认证 API、账号 CLI 或 Todo API。
- 不使用内存 SQLite 作为唯一集成测试数据库。
- 不在应用导入或启动时隐式修改 schema。
