# Todo Task Model API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有布尔完成模型升级为带账号内流水编号、三阶段状态、必填主题和一层父子关系的 Todo API，并安全迁移现有 SQLite 数据。

**Architecture:** 使用一份新的 Alembic migration 回填并重建 `users`/`tasks` 约束；`Task` 保持单表自引用，service 在写事务内分配 `serial`、校验一层关系并维护 `completed_at`。列表 API 以根任务分组做 keyset cursor，子任务通过第二次批量查询组装，所有读取继续绑定当前 `user_id`。

**Tech Stack:** Python 3.13、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、SQLite、pytest

---

## 执行约束

- 从仓库根目录运行所有命令。
- API 任务先于 Web 计划执行；本计划完成前不要修改 `apps/web`。
- 按 TDD 顺序执行每项任务：先写失败测试、确认失败、写最小实现、确认通过。
- 下列提交步骤只记录建议的提交边界；只有用户明确要求“提交”时才执行 `git add` 和 `git commit`。
- 不修改已有 `0001_initial_schema.py`，只新增后继 migration。
- 不处理工作区已有的 `apps/api/data/tickly.db-shm` 和 `apps/api/data/tickly.db-wal`。

## 文件结构

### 新建

- `apps/api/alembic/versions/0002_todo_task_model.py`：旧数据回填、任务表重建和降级语义。

### 修改

- `apps/api/app/models/user.py`：增加内部账号流水计数器。
- `apps/api/app/models/task.py`：定义新任务字段、约束、索引和一层自引用 ORM 关系。
- `apps/api/app/schemas/tasks.py`：定义三状态、树列表、主题和父级候选契约。
- `apps/api/app/services/tasks.py`：负责流水号事务、父子校验、状态时间、树筛选和 cursor。
- `apps/api/app/api/routes/tasks.py`：公开新契约并映射稳定业务错误。
- `apps/api/tests/test_migrations.py`：验证真实 SQLite 文件的升级、回填和有损降级。
- `apps/api/tests/test_models.py`：验证数据库约束、索引和 `ON DELETE SET NULL`。
- `apps/api/tests/test_task_schemas.py`：验证严格输入输出契约。
- `apps/api/tests/test_tasks_service.py`：验证事务、不变量、树查询和 cursor。
- `apps/api/tests/test_tasks_api.py`：验证 HTTP、认证、所有权和 OpenAPI。

## Task 1: 用 migration 固化新字段和旧数据回填

**Files:**
- Create: `apps/api/alembic/versions/0002_todo_task_model.py`
- Modify: `apps/api/tests/test_migrations.py`

- [ ] **Step 1: 写升级回填失败测试**

在 `apps/api/tests/test_migrations.py` 增加一个测试：先升级到 `0001_initial_schema`，用 SQL 插入两个用户和四条旧任务，再升级到 `head`。测试数据必须覆盖空备注、`priority=none`、两个完成状态和相同创建时间。

```python
def test_task_model_migration_backfills_existing_tasks(tmp_path: Path) -> None:
    database_path = tmp_path / "task-model-migration.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001_initial_schema")

    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, created_at, updated_at) "
            "VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01'), "
            "('u2', 'other', 'hash', 'Asia/Shanghai', 1, '2026-08-01', '2026-08-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, title, notes, is_completed, priority, due_at, "
            "completed_at, created_at, updated_at) VALUES "
            "('t1', 'u1', '第一项', NULL, 0, 'none', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 09:00:00'), "
            "('t2', 'u1', '第二项', '详细说明', 1, 'high', NULL, "
            "'2026-08-02 10:00:00', '2026-08-02 09:00:00', '2026-08-02 10:00:00'), "
            "('t3', 'u1', '第三项', '   ', 1, 'low', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 11:00:00'), "
            "('t4', 'u2', '其他账号', NULL, 0, 'medium', NULL, NULL, "
            "'2026-08-02 09:00:00', '2026-08-02 09:00:00')"
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT id, serial, description, priority, topic, status, "
            "completed_at, parent_id FROM tasks ORDER BY user_id, serial"
        ).mappings().all()
        counters = connection.exec_driver_sql(
            "SELECT id, next_task_serial FROM users ORDER BY id"
        ).all()

    assert [dict(row) for row in rows] == [
        {
            "id": "t1",
            "serial": 1,
            "description": "第一项",
            "priority": None,
            "topic": "未分类",
            "status": "new",
            "completed_at": None,
            "parent_id": None,
        },
        {
            "id": "t2",
            "serial": 2,
            "description": "详细说明",
            "priority": "high",
            "topic": "未分类",
            "status": "completed",
            "completed_at": "2026-08-02 10:00:00",
            "parent_id": None,
        },
        {
            "id": "t3",
            "serial": 3,
            "description": "第三项",
            "priority": "low",
            "topic": "未分类",
            "status": "completed",
            "completed_at": "2026-08-02 11:00:00",
            "parent_id": None,
        },
        {
            "id": "t4",
            "serial": 1,
            "description": "其他账号",
            "priority": "medium",
            "topic": "未分类",
            "status": "new",
            "completed_at": None,
            "parent_id": None,
        },
    ]
    assert counters == [("u1", 4), ("u2", 2)]
    engine.dispose()
```

- [ ] **Step 2: 运行测试确认 migration 尚不存在**

Run:

```powershell
mise exec -- pnpm test:api tests/test_migrations.py::test_task_model_migration_backfills_existing_tasks -q
```

Expected: FAIL，原因包含无法找到 revision `0002_todo_task_model` 或新列不存在。

- [ ] **Step 3: 新增 migration 并完成升级逻辑**

创建 `apps/api/alembic/versions/0002_todo_task_model.py`，固定 revision：

```python
"""upgrade todo task model

Revision ID: 0002_todo_task_model
Revises: 0001_initial_schema
Create Date: 2026-08-17
"""

from collections import defaultdict
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_todo_task_model"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_serials() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, user_id FROM tasks "
            "ORDER BY user_id, created_at ASC, id ASC"
        )
    ).mappings()
    counters: dict[str, int] = defaultdict(int)
    for row in rows:
        counters[row["user_id"]] += 1
        connection.execute(
            sa.text("UPDATE tasks SET serial = :serial WHERE id = :id"),
            {"serial": counters[row["user_id"]], "id": row["id"]},
        )
    for user_id, last_serial in counters.items():
        connection.execute(
            sa.text(
                "UPDATE users SET next_task_serial = :next_serial WHERE id = :user_id"
            ),
            {"next_serial": last_serial + 1, "user_id": user_id},
        )


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "next_task_serial",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("tasks", sa.Column("serial", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("topic", sa.String(100), nullable=True))
    op.add_column("tasks", sa.Column("status", sa.String(16), nullable=True))
    op.add_column("tasks", sa.Column("parent_id", sa.String(36), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE tasks SET "
            "description = CASE "
            "WHEN notes IS NULL OR trim(notes) = '' THEN title ELSE notes END, "
            "priority = CASE WHEN priority = 'none' THEN NULL ELSE priority END, "
            "topic = '未分类', "
            "status = CASE WHEN is_completed = 1 THEN 'completed' ELSE 'new' END, "
            "completed_at = CASE "
            "WHEN is_completed = 1 AND completed_at IS NULL THEN updated_at "
            "ELSE completed_at END"
        )
    )
    _backfill_serials()

    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_tasks_priority", type_="check")
        batch_op.drop_constraint("ck_tasks_notes_length", type_="check")
        batch_op.drop_index("ix_tasks_user_completed")
        batch_op.alter_column("serial", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("description", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("topic", existing_type=sa.String(100), nullable=False)
        batch_op.alter_column("status", existing_type=sa.String(16), nullable=False)
        batch_op.alter_column(
            "priority", existing_type=sa.String(16), nullable=True, server_default=None
        )
        batch_op.drop_column("notes")
        batch_op.drop_column("is_completed")
        batch_op.create_unique_constraint(
            "uq_tasks_user_serial", ["user_id", "serial"]
        )
        batch_op.create_check_constraint("ck_tasks_serial_positive", "serial > 0")
        batch_op.create_check_constraint(
            "ck_tasks_description_length",
            "length(description) BETWEEN 1 AND 4000",
        )
        batch_op.create_check_constraint(
            "ck_tasks_topic_length", "length(topic) BETWEEN 1 AND 100"
        )
        batch_op.create_check_constraint(
            "ck_tasks_status",
            "status IN ('new', 'in_progress', 'completed')",
        )
        batch_op.create_check_constraint(
            "ck_tasks_priority",
            "priority IS NULL OR priority IN ('low', 'medium', 'high')",
        )
        batch_op.create_foreign_key(
            "fk_tasks_parent_id_tasks",
            "tasks",
            ["parent_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_tasks_user_status", ["user_id", "status"])
        batch_op.create_index("ix_tasks_user_topic", ["user_id", "topic"])
        batch_op.create_index("ix_tasks_user_parent", ["user_id", "parent_id"])


def downgrade() -> None:
    op.add_column("tasks", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("is_completed", sa.Boolean(), nullable=True),
    )
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE tasks SET notes = description"))
    connection.execute(
        sa.text(
            "UPDATE tasks SET is_completed = CASE "
            "WHEN status = 'completed' THEN 1 ELSE 0 END"
        )
    )
    connection.execute(
        sa.text("UPDATE tasks SET priority = 'none' WHERE priority IS NULL")
    )
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_tasks_parent_id_tasks", type_="foreignkey")
        batch_op.drop_constraint("uq_tasks_user_serial", type_="unique")
        batch_op.drop_constraint("ck_tasks_serial_positive", type_="check")
        batch_op.drop_constraint("ck_tasks_description_length", type_="check")
        batch_op.drop_constraint("ck_tasks_topic_length", type_="check")
        batch_op.drop_constraint("ck_tasks_status", type_="check")
        batch_op.drop_constraint("ck_tasks_priority", type_="check")
        batch_op.drop_index("ix_tasks_user_status")
        batch_op.drop_index("ix_tasks_user_topic")
        batch_op.drop_index("ix_tasks_user_parent")
        batch_op.alter_column(
            "is_completed",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
        batch_op.alter_column(
            "priority",
            existing_type=sa.String(16),
            nullable=False,
            server_default="none",
        )
        batch_op.drop_column("serial")
        batch_op.drop_column("description")
        batch_op.drop_column("topic")
        batch_op.drop_column("status")
        batch_op.drop_column("parent_id")
        batch_op.create_check_constraint(
            "ck_tasks_notes_length", "notes IS NULL OR length(notes) <= 4000"
        )
        batch_op.create_check_constraint(
            "ck_tasks_priority",
            "priority IN ('none', 'low', 'medium', 'high')",
        )
        batch_op.create_index(
            "ix_tasks_user_completed", ["user_id", "is_completed"]
        )
    op.drop_column("users", "next_task_serial")
```

实现时若 Alembic 在 SQLite batch 反射阶段报告旧 CHECK 约束名称不可用，先用当前临时数据库的 `inspect(engine).get_check_constraints("tasks")` 核对实际名称，再保持 migration 与 `0001_initial_schema.py` 中名称一致；不要静默跳过约束。

- [ ] **Step 4: 增加降级语义断言**

在同一测试末尾执行降级并断言：

```python
    command.downgrade(config, "0001_initial_schema")
    with engine.connect() as connection:
        downgraded = connection.exec_driver_sql(
            "SELECT id, notes, is_completed, priority FROM tasks ORDER BY id"
        ).mappings().all()
    assert [dict(row) for row in downgraded] == [
        {"id": "t1", "notes": "第一项", "is_completed": 0, "priority": "none"},
        {"id": "t2", "notes": "详细说明", "is_completed": 1, "priority": "high"},
        {"id": "t3", "notes": "第三项", "is_completed": 1, "priority": "low"},
        {"id": "t4", "notes": "其他账号", "is_completed": 0, "priority": "medium"},
    ]
```

- [ ] **Step 5: 运行 migration 测试**

Run:

```powershell
mise exec -- pnpm test:api tests/test_migrations.py -q
```

Expected: PASS，所有 migration 测试通过。

- [ ] **Step 6: 条件式提交检查点**

仅在用户明确要求提交时执行：

```powershell
git add -- apps/api/alembic/versions/0002_todo_task_model.py apps/api/tests/test_migrations.py
git commit -m "feat(api): 迁移新版待办数据模型"
```

## Task 2: 让 ORM 与新数据库结构一致

**Files:**
- Modify: `apps/api/app/models/user.py`
- Modify: `apps/api/app/models/task.py`
- Modify: `apps/api/tests/test_models.py`

- [ ] **Step 1: 改写模型约束测试**

把 `test_models_expose_required_tables_and_task_indexes` 和 `test_task_defaults_and_constraints` 更新为新字段，并增加删除父任务提升子任务的测试：

```python
def test_deleting_parent_promotes_children_to_roots(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        session.add(user)
        session.flush()
        parent = Task(
            user_id=user.id,
            serial=1,
            title="父任务",
            description="父任务",
            topic="Tickly",
        )
        child = Task(
            user_id=user.id,
            serial=2,
            title="子任务",
            description="子任务",
            topic="Tickly",
            parent=parent,
        )
        session.add_all([parent, child])
        session.commit()
        child_id = child.id

        session.delete(parent)
        session.commit()
        session.expire_all()

        promoted = session.get(Task, child_id)
        assert promoted is not None
        assert promoted.parent_id is None
    engine.dispose()
```

同时断言索引和唯一约束：

```python
    task_indexes = {index["name"] for index in inspect(engine).get_indexes("tasks")}
    task_uniques = {
        item["name"] for item in inspect(engine).get_unique_constraints("tasks")
    }
    assert {
        "ix_tasks_user_status",
        "ix_tasks_user_topic",
        "ix_tasks_user_parent",
        "ix_tasks_user_due",
        "ix_tasks_user_created",
    } <= task_indexes
    assert "uq_tasks_user_serial" in task_uniques
```

- [ ] **Step 2: 运行模型测试确认旧 ORM 失败**

Run:

```powershell
mise exec -- pnpm test:api tests/test_models.py -q
```

Expected: FAIL，原因包含 `Task` 不接受 `serial`/`description`/`topic` 或缺少新索引。

- [ ] **Step 3: 更新 User 与 Task ORM**

在 `User` 增加：

```python
    next_task_serial: Mapped[int] = mapped_column(nullable=False, default=1)
```

把 `Task` 字段和关系改为：

```python
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    serial: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="tasks")
    parent: Mapped["Task | None"] = relationship(
        back_populates="children", remote_side="Task.id"
    )
    children: Mapped[list["Task"]] = relationship(
        back_populates="parent", passive_deletes=True
    )
```

同步把 `__table_args__` 改为 migration 中同名约束和索引；中文注释必须说明一层关系仍由 service 校验，数据库自引用外键只负责引用完整性和删除提升。

- [ ] **Step 4: 更新模型 fixture 并运行测试**

所有直接构造 `Task` 的测试必须显式提供 `serial`、`description` 和 `topic`，例如：

```python
Task(
    user_id=user.id,
    serial=1,
    title="Inbox item",
    description="Inbox item",
    topic="未分类",
)
```

Run:

```powershell
mise exec -- pnpm test:api tests/test_models.py -q
```

Expected: PASS。

- [ ] **Step 5: 条件式提交检查点**

```powershell
git add -- apps/api/app/models/user.py apps/api/app/models/task.py apps/api/tests/test_models.py
git commit -m "feat(api): 定义待办流水号与父子关系"
```

## Task 3: 定义严格的新请求与响应契约

**Files:**
- Modify: `apps/api/app/schemas/tasks.py`
- Modify: `apps/api/tests/test_task_schemas.py`

- [ ] **Step 1: 先写 schema 行为测试**

用以下核心断言替换旧 `notes`、`is_completed` 契约：

```python
def test_create_request_normalizes_required_fields_and_description_default() -> None:
    payload = TaskCreateRequest.model_validate(
        {
            "title": "  调整布局  ",
            "description": "   ",
            "topic": "  Tickly  ",
            "priority": None,
        }
    )
    assert payload.title == "调整布局"
    assert payload.description == "调整布局"
    assert payload.topic == "Tickly"
    assert payload.priority is None


def test_update_request_rejects_clearing_required_fields() -> None:
    for field in ("title", "description", "topic", "status"):
        with pytest.raises(ValidationError):
            TaskUpdateRequest.model_validate({field: None})
    for field in ("description", "topic"):
        with pytest.raises(ValidationError):
            TaskUpdateRequest.model_validate({field: "   "})


def test_task_list_query_binds_topic_and_tree_cursor() -> None:
    query = TaskListQuery(topic="  Tickly  ")
    assert query.status is TaskStatusFilter.ALL
    assert query.topic == "Tickly"
    assert query.sort is TaskSort.CREATED_AT
```

响应测试用一个完整存储对象断言 `serial`、`description`、`topic`、`status` 和 `parent_id`，并继续断言 `user_id`、`next_task_serial` 不在 JSON 中。

- [ ] **Step 2: 运行 schema 测试确认失败**

Run:

```powershell
mise exec -- pnpm test:api tests/test_task_schemas.py -q
```

Expected: FAIL，原因包含新 enum 或新字段不存在。

- [ ] **Step 3: 实现 enum、输入和列表 query**

在 `apps/api/app/schemas/tasks.py` 定义：

```python
class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskStatusFilter(StrEnum):
    ALL = "all"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskSort(StrEnum):
    SERIAL = "serial"
    CREATED_AT = "created_at"
    DUE_AT = "due_at"
    PRIORITY = "priority"
```

创建 schema 使用 `model_validator(mode="after")` 在字段校验后填充描述：

```python
class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    topic: str = Field(min_length=1, max_length=100)
    due_at: datetime | None = None
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("title", "topic", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def default_description(self) -> Self:
        if self.description is None:
            self.description = self.title
        return self
```

`TaskUpdateRequest` 对 `description`、`topic` 和 `status` 显式 `null` 做拒绝，对 `priority`、`due_at` 和 `parent_id` 保留显式清空；空 PATCH 继续拒绝。

`TaskListQuery` 增加规范化的可空 `topic`，并把状态类型换成 `TaskStatusFilter`。另外新增：

```python
class ParentOptionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)
```

- [ ] **Step 4: 实现响应模型**

```python
class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    serial: int
    title: str
    description: str
    priority: TaskPriority | None
    topic: str
    status: TaskStatus
    due_at: datetime | None
    completed_at: datetime | None
    parent_id: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "due_at", "completed_at", "created_at", "updated_at", mode="before"
    )
    @classmethod
    def normalize_stored_datetime(cls, value: datetime | None) -> datetime | None:
        return _stored_utc(value)


class TaskGroupResponse(BaseModel):
    task: TaskResponse
    children: list[TaskResponse]
    child_count: int
    completed_child_count: int
    context_only: bool


class TaskListResponse(BaseModel):
    items: list[TaskGroupResponse]
    next_cursor: str | None


class TaskDetailResponse(TaskResponse):
    children: list[TaskResponse]


class TopicListResponse(BaseModel):
    items: list[str]


class ParentOptionResponse(BaseModel):
    id: str
    serial: int
    title: str
    topic: str
    status: TaskStatus

    model_config = ConfigDict(from_attributes=True)


class ParentOptionPageResponse(BaseModel):
    items: list[ParentOptionResponse]
    next_cursor: str | None
```

- [ ] **Step 5: 运行 schema 测试**

Run:

```powershell
mise exec -- pnpm test:api tests/test_task_schemas.py -q
```

Expected: PASS。

- [ ] **Step 6: 条件式提交检查点**

```powershell
git add -- apps/api/app/schemas/tasks.py apps/api/tests/test_task_schemas.py
git commit -m "feat(api): 更新待办输入输出契约"
```

## Task 4: 实现流水号、状态时间和一层父子写入规则

**Files:**
- Modify: `apps/api/app/services/tasks.py`
- Modify: `apps/api/tests/test_tasks_service.py`

- [ ] **Step 1: 重写 service fixture 并写失败测试**

把测试辅助函数改为接收新字段：

```python
def add_task(
    session: Session,
    user_id: str,
    task_id: str,
    title: str,
    *,
    serial: int,
    created_at: datetime,
    topic: str = "Tickly",
    status: str = "new",
    parent_id: str | None = None,
    due_at: datetime | None = None,
    priority: str | None = None,
) -> Task:
    task = Task(
        id=task_id,
        user_id=user_id,
        serial=serial,
        title=title,
        description=title,
        topic=topic,
        status=status,
        parent_id=parent_id,
        created_at=created_at,
        updated_at=created_at,
        due_at=due_at,
        priority=priority,
        completed_at=created_at if status == "completed" else None,
    )
    session.add(task)
    session.commit()
    return task
```

新增四组测试：

1. 两个账号分别从 `serial=1` 创建，单账号连续创建为 1、2，删除后下一条为 3。
2. 创建时空描述已在 schema 中变成标题，修改标题后描述不变化。
3. `new -> in_progress -> completed -> new -> completed` 对完成时间的写入和清空。
4. 跨用户父任务、子任务作为父任务、自引用和“已有子任务的根任务成为子任务”全部抛出 `InvalidTaskRelationship` 且回滚。

状态时间测试固定 `utc_now`：

```python
def test_status_transitions_control_completed_at(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = add_user(session, "owner")
    task = create_task(
        session,
        owner.id,
        TaskCreateRequest(title="状态任务", topic="Tickly"),
    )
    completed_time = datetime(2026, 8, 17, 10, tzinfo=UTC)
    reopened_time = datetime(2026, 8, 17, 11, tzinfo=UTC)
    monkeypatch.setattr("app.services.tasks.utc_now", lambda: completed_time)

    in_progress = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="in_progress"),
    )
    assert in_progress.completed_at is None
    completed = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="completed"),
    )
    assert completed.completed_at == completed_time
    repeated = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="completed"),
    )
    assert repeated.completed_at == completed_time

    monkeypatch.setattr("app.services.tasks.utc_now", lambda: reopened_time)
    reopened = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="new"),
    )
    assert reopened.completed_at is None
    completed_again = update_task(
        session,
        owner.id,
        task.id,
        TaskUpdateRequest(status="completed"),
    )
    assert completed_again.completed_at == reopened_time
```

- [ ] **Step 2: 运行写入用例确认失败**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_service.py -q
```

Expected: FAIL，旧 service 仍引用 `notes`、`is_completed` 和 `priority.value`。

- [ ] **Step 3: 实现原子 serial 分配**

导入 `update` 和 `User`，新增：

```python
def _allocate_serial(session: Session, user_id: str) -> int:
    next_serial = session.scalar(
        update(User)
        .where(User.id == user_id)
        .values(next_task_serial=User.next_task_serial + 1)
        .returning(User.next_task_serial)
    )
    if next_serial is None:
        raise TaskNotFound
    return next_serial - 1
```

`create_task` 必须在同一 try/commit/rollback 边界内先分配 serial，再构造任务：

```python
def create_task(session: Session, user_id: str, payload: TaskCreateRequest) -> Task:
    """在单一事务中分配账号流水号、校验父级并创建 new 状态任务。"""

    try:
        if payload.parent_id is not None:
            _require_valid_parent(session, user_id, payload.parent_id)
        now = utc_now()
        task = Task(
            user_id=user_id,
            serial=_allocate_serial(session, user_id),
            title=payload.title,
            description=payload.description,
            priority=(payload.priority.value if payload.priority is not None else None),
            topic=payload.topic,
            status="new",
            due_at=payload.due_at,
            completed_at=None,
            parent_id=payload.parent_id,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task
    except Exception:
        session.rollback()
        raise
```

- [ ] **Step 4: 实现父级校验和状态更新**

```python
class InvalidTaskRelationship(Exception):
    """父待办不存在、跨用户或违反一层层级。"""


def _require_valid_parent(
    session: Session,
    user_id: str,
    parent_id: str,
    *,
    task_id: str | None = None,
) -> Task:
    parent = session.scalar(
        select(Task).where(Task.id == parent_id, Task.user_id == user_id)
    )
    if parent is None or parent.parent_id is not None or parent.id == task_id:
        raise InvalidTaskRelationship
    return parent
```

更新 `parent_id` 前，如果目标任务已有子任务则拒绝：

```python
        if "parent_id" in fields:
            if payload.parent_id is not None:
                _require_valid_parent(
                    session, user_id, payload.parent_id, task_id=task.id
                )
                has_children = session.scalar(
                    select(Task.id).where(
                        Task.user_id == user_id,
                        Task.parent_id == task.id,
                    ).limit(1)
                )
                if has_children is not None:
                    raise InvalidTaskRelationship
            task.parent_id = payload.parent_id
```

状态逻辑使用：

```python
        now = utc_now()
        if "status" in fields:
            next_status = payload.status.value
            if next_status == "completed" and task.status != "completed":
                task.completed_at = now
            elif next_status != "completed":
                task.completed_at = None
            task.status = next_status
        task.updated_at = now
```

其余字段按 `model_fields_set` 做最小 PATCH；`priority=None` 直接写空，`description`、`topic` 和 `status` 已由 schema 保证非空。

- [ ] **Step 5: 运行写入与回滚测试**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_service.py -q
```

Expected: 新写入测试通过；尚未迁移的旧列表测试可以继续失败，失败应只集中在 Task 5 要替换的筛选和 cursor 断言。

- [ ] **Step 6: 条件式提交检查点**

```powershell
git add -- apps/api/app/services/tasks.py apps/api/tests/test_tasks_service.py
git commit -m "feat(api): 实现待办状态与父子写入规则"
```

## Task 5: 实现根任务分组筛选和稳定 cursor

**Files:**
- Modify: `apps/api/app/services/tasks.py`
- Modify: `apps/api/tests/test_tasks_service.py`

- [ ] **Step 1: 写树筛选和分页失败测试**

构造以下数据：

```text
#1 root-new       topic=Tickly status=new
├─ #2 child-done  topic=Tickly status=completed
└─ #3 child-work  topic=Work   status=in_progress
#4 root-done      topic=Work   status=completed
#5 other-user     topic=Tickly status=new
```

断言：

- `status=new` 返回 #1 及全部两个子任务。
- `status=in_progress` 返回 #1，`context_only=True`，children 只有 #3。
- `topic=Work,status=completed` 只返回 #4。
- `topic=Tickly,status=completed` 返回 #1 作为上下文，children 只有 #2。
- `limit=1` 的 cursor 以根分组分页，任何响应都不出现 #5。
- cursor 从 `topic=Tickly` 复用到 `topic=Work` 返回 `InvalidCursor`。

- [ ] **Step 2: 运行树列表测试确认失败**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_service.py -q
```

Expected: FAIL，旧 `TaskPage.items` 仍是平坦 `Task`。

- [ ] **Step 3: 定义 service 返回值和匹配条件**

```python
@dataclass(frozen=True)
class TaskGroup:
    task: Task
    children: list[Task]
    child_count: int
    completed_child_count: int
    context_only: bool


@dataclass(frozen=True)
class TaskPage:
    items: list[TaskGroup]
    next_cursor: str | None


def _matches_task(task: Task, query: TaskListQuery) -> bool:
    status_matches = (
        query.status is TaskStatusFilter.ALL or task.status == query.status.value
    )
    topic_matches = query.topic is None or task.topic == query.topic
    return status_matches and topic_matches
```

扩展 `_CursorPayload` 加入 `topic: str | None`，把 `serial` 作为整数 sort value；priority 排名使用 `{None: 0, "low": 1, "medium": 2, "high": 3}`，并为 `None` 建立 null bucket，使可空 priority 始终排末尾。

- [ ] **Step 4: 先分页根任务，再批量组装 children**

根任务资格使用 `exists()`：根自身匹配，或者存在匹配的直接子任务。查询始终包含 `Task.parent_id.is_(None)` 和 `Task.user_id == user_id`。

```python
child_match = aliased(Task)
root_predicates = []
child_predicates = []
if query.status is not TaskStatusFilter.ALL:
    root_predicates.append(Task.status == query.status.value)
    child_predicates.append(child_match.status == query.status.value)
if query.topic is not None:
    root_predicates.append(Task.topic == query.topic)
    child_predicates.append(child_match.topic == query.topic)
root_matches = and_(*root_predicates) if root_predicates else true()
matching_child_exists = exists(
    select(child_match.id).where(
        child_match.user_id == user_id,
        child_match.parent_id == Task.id,
        *child_predicates,
    )
)
statement = select(Task).where(
    Task.user_id == user_id,
    Task.parent_id.is_(None),
    or_(root_matches, matching_child_exists),
)
```

从 SQLAlchemy 导入 `exists`、`true`，从 `sqlalchemy.orm` 导入 `aliased`。

核心组装代码：

```python
    roots = list(session.scalars(statement.limit(query.limit + 1)).all())
    has_more = len(roots) > query.limit
    page_roots = roots[: query.limit]
    root_ids = [task.id for task in page_roots]
    child_rows = list(
        session.scalars(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.parent_id.in_(root_ids),
            )
            .order_by(Task.serial.asc())
        ).all()
    ) if root_ids else []
    children_by_parent: dict[str, list[Task]] = {root_id: [] for root_id in root_ids}
    for child in child_rows:
        if child.parent_id is not None:
            children_by_parent[child.parent_id].append(child)

    groups: list[TaskGroup] = []
    for root in page_roots:
        root_matches = _matches_task(root, query)
        children = children_by_parent[root.id]
        groups.append(
            TaskGroup(
                task=root,
                children=(
                    children
                    if root_matches
                    else [child for child in children if _matches_task(child, query)]
                ),
                child_count=len(children),
                completed_child_count=sum(
                    child.status == "completed" for child in children
                ),
                context_only=not root_matches,
            )
        )
```

`next_cursor` 必须从 `page_roots[-1]` 编码，而不是从最后一个子任务编码。

- [ ] **Step 5: 运行全部 service 测试**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_service.py -q
```

Expected: PASS。

- [ ] **Step 6: 条件式提交检查点**

```powershell
git add -- apps/api/app/services/tasks.py apps/api/tests/test_tasks_service.py
git commit -m "feat(api): 支持待办树筛选与分页"
```

## Task 6: 增加主题和父待办候选 service

**Files:**
- Modify: `apps/api/app/services/tasks.py`
- Modify: `apps/api/tests/test_tasks_service.py`

- [ ] **Step 1: 写主题和父级候选失败测试**

测试必须覆盖：

- 主题仅返回当前用户值，保留 `Tickly` 与 `tickly` 两个不同字符串，展示排序不区分大小写。
- 父级候选只返回根任务，不返回子任务或其他用户任务。
- `query="#18"` 和 `query="18"` 精确匹配 serial。
- 其他 query 按标题包含匹配。
- 候选 cursor 不能跨 query 复用。

- [ ] **Step 2: 实现主题列表**

```python
def list_topics(session: Session, user_id: str) -> list[str]:
    return list(
        session.scalars(
            select(Task.topic)
            .where(Task.user_id == user_id)
            .distinct()
            .order_by(Task.topic.collate("NOCASE"), Task.topic)
        ).all()
    )
```

- [ ] **Step 3: 实现父级候选分页**

新增 `ParentOptionPage` dataclass，并使用独立 cursor payload 绑定规范化后的 query。serial query 解析规则：

```python
def _parent_serial_query(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    digits = normalized[1:] if normalized.startswith("#") else normalized
    return int(digits) if digits.isdigit() else None
```

候选 SQL 始终包含：

```python
select(Task).where(
    Task.user_id == user_id,
    Task.parent_id.is_(None),
)
```

数字 query 增加 `Task.serial == serial`，文本 query 增加 `Task.title.contains(normalized_query)`；按 `Task.serial.asc(), Task.id.asc()` 做 cursor 分页。

- [ ] **Step 4: 运行 service 测试**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_service.py -q
```

Expected: PASS。

- [ ] **Step 5: 条件式提交检查点**

```powershell
git add -- apps/api/app/services/tasks.py apps/api/tests/test_tasks_service.py
git commit -m "feat(api): 提供主题与父待办候选查询"
```

## Task 7: 公开 HTTP 契约并保护所有权边界

**Files:**
- Modify: `apps/api/app/api/routes/tasks.py`
- Modify: `apps/api/tests/test_tasks_api.py`

- [ ] **Step 1: 重写 HTTP 契约测试**

更新 CRUD 测试请求：

```python
created = task_client.post(
    "/api/v1/tasks",
    headers=headers,
    json={
        "title": "  第一项任务  ",
        "description": "",
        "topic": "Tickly",
        "priority": "high",
        "due_at": "2026-07-30T18:00:00+08:00",
    },
)
assert created.status_code == 201
assert created.json()["serial"] == 1
assert created.json()["description"] == "第一项任务"
assert created.json()["status"] == "new"
```

增加：

- PATCH `status=completed` 写入完成时间，PATCH `status=in_progress` 清空。
- 创建跨用户或二层父级返回统一 `422 invalid_task_relationship`，响应不泄漏父任务信息。
- `GET /topics` 和 `GET /parent-options` 必须认证且只返回当前用户数据。
- 列表返回 `TaskGroupResponse`，子任务命中筛选时父任务 `context_only=true`。
- OpenAPI 包含 `/tasks/topics` 和 `/tasks/parent-options`，且静态路由不被 `/{task_id}` 吞掉。

- [ ] **Step 2: 运行 API 测试确认旧路由失败**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_api.py -q
```

Expected: FAIL，旧路由响应仍是平坦 items 且没有新端点。

- [ ] **Step 3: 在动态 task_id 路由之前声明静态路由**

在 `@router.get("/{task_id}")` 之前添加：

```python
ParentQuery = Annotated[ParentOptionQuery, Query()]


@router.get("/topics", response_model=TopicListResponse)
def topics(session: DbSession, user: CurrentUser) -> TopicListResponse:
    return TopicListResponse(items=list_topics(session, user.id))


@router.get("/parent-options", response_model=ParentOptionPageResponse)
def parent_options(
    query: ParentQuery,
    session: DbSession,
    user: CurrentUser,
) -> ParentOptionPageResponse:
    try:
        page = list_parent_options(session, user.id, query)
    except InvalidCursor as error:
        raise _invalid_cursor() from error
    return ParentOptionPageResponse(
        items=[ParentOptionResponse.model_validate(task) for task in page.items],
        next_cursor=page.next_cursor,
    )
```

列表 route 映射 groups：

```python
    return TaskListResponse(
        items=[
            TaskGroupResponse(
                task=TaskResponse.model_validate(group.task),
                children=[
                    TaskResponse.model_validate(child) for child in group.children
                ],
                child_count=group.child_count,
                completed_child_count=group.completed_child_count,
                context_only=group.context_only,
            )
            for group in page.items
        ],
        next_cursor=page.next_cursor,
    )
```

详情 service 必须再次把直接子任务绑定当前用户，不能依赖 ORM relationship 隐式展开：

```python
@dataclass(frozen=True)
class TaskDetail:
    task: Task
    children: list[Task]


def get_task_detail(session: Session, user_id: str, task_id: str) -> TaskDetail:
    task = get_task(session, user_id, task_id)
    children = list(
        session.scalars(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.parent_id == task.id,
            )
            .order_by(Task.serial.asc())
        ).all()
    )
    return TaskDetail(task=task, children=children)
```

详情 route 返回：

```python
@router.get("/{task_id}", response_model=TaskDetailResponse)
def detail(task_id: str, session: DbSession, user: CurrentUser) -> TaskDetailResponse:
    try:
        result = get_task_detail(session, user.id, task_id)
    except TaskNotFound as error:
        raise _task_not_found() from error
    return TaskDetailResponse(
        **TaskResponse.model_validate(result.task).model_dump(),
        children=[
            TaskResponse.model_validate(child) for child in result.children
        ],
    )
```

子任务详情因查询不到 `parent_id == child.id` 的合法二层记录而返回空 children；即使数据库存在异常跨用户引用，也不能进入响应。

- [ ] **Step 4: 映射父级业务错误**

create/update 捕获 `InvalidTaskRelationship` 并返回：

```python
def _invalid_task_relationship() -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_task_relationship",
        message="父待办关系无效",
    )
```

跨用户、不存在、自引用和二层父级必须共享该响应，不能分别暴露原因。

- [ ] **Step 5: 运行 API 契约测试**

Run:

```powershell
mise exec -- pnpm test:api tests/test_tasks_api.py -q
```

Expected: PASS。

- [ ] **Step 6: 条件式提交检查点**

```powershell
git add -- apps/api/app/api/routes/tasks.py apps/api/tests/test_tasks_api.py
git commit -m "feat(api): 公开新版待办树接口"
```

## Task 8: API 全量回归与契约冻结

**Files:**
- Verify only

- [ ] **Step 1: 运行 API 全量测试**

Run:

```powershell
mise exec -- pnpm test:api
```

Expected: PASS，pytest 无失败。

- [ ] **Step 2: 检查旧字段和所有权条件**

Run:

```powershell
rg -n "notes|is_completed|TaskStatus\.ACTIVE|priority.*none" apps/api/app apps/api/tests
```

Expected: 只允许 migration 的升级/降级兼容代码和明确描述旧结构的测试准备命中；生产 model、schema、service 和 route 不应命中旧字段。

Run:

```powershell
rg -n "Task\.id == task_id.*Task\.user_id == user_id|Task\.user_id == user_id.*Task\.id == task_id" apps/api/app/services/tasks.py
```

Expected: 单任务读取仍在同一个 SQL 条件中绑定任务 ID 和当前用户。

- [ ] **Step 3: 检查 migration head 和工作区**

Run:

```powershell
mise exec -- uv run --project apps/api alembic -c apps/api/alembic.ini heads
git diff --check
git status --short
```

Expected: Alembic 仅有 `0002_todo_task_model (head)`；`git diff --check` 无输出；状态只包含本计划文件、API 目标改动和进入任务前已有的未跟踪文件。

- [ ] **Step 4: 等待 Web 计划**

API 回归通过后再执行 `docs/superpowers/plans/2026-08-17-todo-list-web-layout.md`。在 Web 计划完成前，不把 README 或路线图描述为新版 UI 已完成。
