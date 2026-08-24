# Todo Cancelled Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Tickly 增加可恢复的 `cancelled` 状态、父任务原子级联废弃、已处理子任务统计，以及 Web、MCP 和 AI Skill 的完整提示与核验能力。

**Architecture:** `apps/api` 继续作为状态与父子级联的唯一业务权威，使用 Alembic 扩展 SQLite 约束并在单事务内更新父任务和未完成直接子任务。Web 与 MCP 只消费扩展后的 HTTP 契约；Web 通过服务端重读获得级联结果，Skill 在 MCP 写入前后分别负责影响提示、确认和真实结果核验。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、SQLAlchemy、Alembic、SQLite、官方 MCP Python SDK v2、React 19、TypeScript、Vite、Vitest、Testing Library、pnpm、uv。

---

## 文件职责

### API

- Create: `apps/api/alembic/versions/0003_add_cancelled_task_status.py`：扩展/回退数据库状态检查约束。
- Modify: `apps/api/app/models/task.py`：模型元数据中的四状态检查约束。
- Modify: `apps/api/app/schemas/tasks.py`：公开 API 状态枚举与 `resolved_child_count` 响应字段。
- Modify: `apps/api/app/services/tasks.py`：状态时间不变量、父子级联事务和完整子任务统计。
- Modify: `apps/api/app/api/routes/tasks.py`：公开任务组映射新增统计字段。
- Modify: `apps/api/app/api/routes/mcp_tasks.py`：内部 MCP 任务组映射新增统计字段。
- Test: `apps/api/tests/test_migrations.py`、`test_models.py`、`test_task_schemas.py`、`test_tasks_service.py`、`test_tasks_api.py`、`test_mcp_tasks_api.py`。

### MCP

- Modify: `apps/mcp/app/schemas.py`：第四状态、筛选值和任务组统计字段。
- Modify: `apps/mcp/app/tools.py`：状态工具说明与扩展契约。
- Test: `apps/mcp/tests/test_tools.py`、`test_api_client.py`。

### Web

- Modify: `apps/web/src/features/tasks/task-api.ts`：第四状态和任务组统计类型。
- Modify: `apps/web/src/features/tasks/use-task-workspace.ts`：URL 恢复、局部统计与状态刷新。
- Modify: `apps/web/src/features/tasks/task-filter-controls.tsx`、`todo-workspace.tsx`：筛选与摘要标签。
- Modify: `apps/web/src/features/tasks/task-list.tsx`：废弃空状态。
- Modify: `apps/web/src/features/tasks/task-group.tsx`、`task-row.tsx`：已处理进度和状态选项。
- Modify: `apps/web/src/features/tasks/task-editor-panel.tsx`：编辑状态选项。
- Modify: `apps/web/src/index.css`：废弃任务的弱化样式。
- Test: 对应 `task-api.test.ts`、`use-task-workspace.test.tsx`、`task-table.test.tsx`、`task-editor-panel.test.tsx`、`todo-workspace.test.tsx`。

### Skill 与当前事实文档

- Modify: `skills/tickly-todo-mcp/SKILL.md`、`skills/tickly-todo-mcp/evals/evals.json`。
- Modify: `AGENTS.md`、`README.md`、`docs/mcp.md`。

提交步骤只在用户明确说“提交”后执行；在此之前保留工作树改动并持续验证。

## Task 1: 锁定数据库与 schema 的第四状态契约

**Files:**
- Create: `apps/api/alembic/versions/0003_add_cancelled_task_status.py`
- Modify: `apps/api/app/models/task.py`
- Modify: `apps/api/app/schemas/tasks.py`
- Test: `apps/api/tests/test_migrations.py`
- Test: `apps/api/tests/test_models.py`
- Test: `apps/api/tests/test_task_schemas.py`

- [ ] **Step 1: 先写 enum、模型约束和 migration 升降级失败测试**

在 `test_task_schemas.py` 将状态期望改为：

```python
assert [item.value for item in TaskStatus] == [
    "new",
    "in_progress",
    "completed",
    "cancelled",
]
assert [item.value for item in TaskStatusFilter] == [
    "all",
    "new",
    "in_progress",
    "completed",
    "cancelled",
]
```

在 `test_models.py` 把 `ck_tasks_status` 的期望更新为四状态，并增加插入
`status="cancelled"` 成功、插入未知状态失败的断言。

在 `test_migrations.py` 增加：

```python
def test_cancelled_status_migration_upgrades_and_downgrades_lossily(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cancelled-status.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0002_todo_task_model")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, "
            "next_task_serial, created_at, updated_at) VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, 2, "
            "'2026-08-24', '2026-08-24')"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks "
            "(id, user_id, serial, title, description, topic, status, "
            "created_at, updated_at) VALUES "
            "('t1', 'u1', 1, '废弃任务', '废弃任务', 'Tickly', 'new', "
            "'2026-08-24', '2026-08-24')"
        )

    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE tasks SET status = 'cancelled' WHERE id = 't1'"
        )
    command.downgrade(config, "0002_todo_task_model")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT status FROM tasks WHERE id = 't1'"
        ).scalar_one() == "new"
    engine.dispose()
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
mise exec -- pnpm test:api -- tests/test_task_schemas.py tests/test_models.py tests/test_migrations.py -q
```

Expected: FAIL，`TaskStatus`/数据库约束缺少 `cancelled`，且 revision `0003` 尚不存在。

- [ ] **Step 3: 实现 enum、模型约束和 0003 migration**

在 `tasks.py` 增加：

```python
class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskStatusFilter(StrEnum):
    ALL = "all"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
```

模型约束统一改为：

```python
CheckConstraint(
    "status IN ('new', 'in_progress', 'completed', 'cancelled')",
    name="ck_tasks_status",
)
```

创建 `0003_add_cancelled_task_status.py`：

```python
"""增加待办废弃状态。

Revision ID: 0003_add_cancelled_task_status
Revises: 0002_todo_task_model
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_add_cancelled_task_status"
down_revision: str | Sequence[str] | None = "0002_todo_task_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _reject_unknown_statuses() -> None:
    connection = op.get_bind()
    tasks = sa.table("tasks", sa.column("status"))
    unknown = connection.execute(
        sa.select(tasks.c.status)
        .where(tasks.c.status.not_in(("new", "in_progress", "completed")))
        .limit(1)
    ).first()
    if unknown is not None:
        raise RuntimeError("tasks 存在无法迁移的未知状态")


def _replace_status_constraint(values: str) -> None:
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_tasks_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tasks_status",
            f"status IN ({values})",
        )


def upgrade() -> None:
    # SQLite batch DDL 不能整体回滚，必须在首个 DDL 前拒绝未知历史状态。
    _reject_unknown_statuses()
    _replace_status_constraint(
        "'new', 'in_progress', 'completed', 'cancelled'"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE tasks SET status = 'new', completed_at = NULL "
            "WHERE status = 'cancelled'"
        )
    )
    _replace_status_constraint("'new', 'in_progress', 'completed'")
```

- [ ] **Step 4: 运行 Task 1 测试并确认 GREEN**

Run:

```powershell
mise exec -- pnpm test:api -- tests/test_task_schemas.py tests/test_models.py tests/test_migrations.py -q
```

Expected: PASS；migration head 为 `0003`，升级接受 `cancelled`，降级映射为 `new`。

- [ ] **Step 5: 用户明确授权后提交 Task 1**

```powershell
git add -- apps/api/alembic/versions/0003_add_cancelled_task_status.py apps/api/app/models/task.py apps/api/app/schemas/tasks.py apps/api/tests/test_migrations.py apps/api/tests/test_models.py apps/api/tests/test_task_schemas.py
git commit -m "feat(api): 增加待办废弃状态契约"
```

## Task 2: 实现父子级联事务与已处理统计

**Files:**
- Modify: `apps/api/app/schemas/tasks.py`
- Modify: `apps/api/app/services/tasks.py`
- Test: `apps/api/tests/test_task_schemas.py`
- Test: `apps/api/tests/test_tasks_service.py`

- [ ] **Step 1: 写父子级联、恢复、回滚和统计失败测试**

在 `test_tasks_service.py` 增加四个中文说明测试：

```python
def test_cancelling_parent_cascades_unfinished_children_only(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = add_user(session, "owner")
    parent = add_task(session, owner.id, "00000000-0000-0000-0000-000000000001", "父任务", serial=1)
    new_child = add_task(session, owner.id, "00000000-0000-0000-0000-000000000002", "新子任务", serial=2, parent_id=parent.id)
    active_child = add_task(session, owner.id, "00000000-0000-0000-0000-000000000003", "进行中子任务", serial=3, parent_id=parent.id, status="in_progress")
    completed_child = add_task(session, owner.id, "00000000-0000-0000-0000-000000000004", "完成子任务", serial=4, parent_id=parent.id, status="completed")
    cancelled_child = add_task(session, owner.id, "00000000-0000-0000-0000-000000000005", "已废弃子任务", serial=5, parent_id=parent.id, status="cancelled")
    completed_updated_at = completed_child.updated_at
    cancelled_updated_at = cancelled_child.updated_at
    now = datetime(2026, 8, 24, 10, tzinfo=UTC)
    monkeypatch.setattr("app.services.tasks.utc_now", lambda: now)

    update_task(session, owner.id, parent.id, TaskUpdateRequest(status="cancelled"))

    for task in (parent, new_child, active_child):
        session.refresh(task)
        assert task.status == "cancelled"
        assert task.completed_at is None
        assert task.updated_at == now
    session.refresh(completed_child)
    session.refresh(cancelled_child)
    assert completed_child.status == "completed"
    assert completed_child.updated_at == completed_updated_at
    assert cancelled_child.status == "cancelled"
    assert cancelled_child.updated_at == cancelled_updated_at
```

另加：

- `test_restoring_cancelled_parent_does_not_restore_children`：父任务切回 `new`，子任务仍为 `cancelled`。
- `test_cancelling_parent_rolls_back_parent_and_children_on_commit_failure`：替换 `session.commit` 抛 `IntegrityError`，调用失败后重新查询父子均保持原状态。
- 在现有列表测试中构造 `completed`、`cancelled`、`new` 三个子任务，断言 `child_count == 3`、`completed_child_count == 1`、`resolved_child_count == 2`，且上下文根的统计仍基于完整集合。

在 `test_task_schemas.py` 的 `TaskGroupResponse` 构造中加入
`resolved_child_count=2` 并断言序列化值。

- [ ] **Step 2: 运行 service/schema 测试并确认 RED**

```powershell
mise exec -- pnpm test:api -- tests/test_tasks_service.py tests/test_task_schemas.py -q
```

Expected: FAIL，父任务不会级联，且 `resolved_child_count` 尚不存在。

- [ ] **Step 3: 实现统一状态转换和级联**

在 `TaskGroup` 和 `TaskGroupResponse` 增加：

```python
resolved_child_count: int
```

在 `tasks.py` service 中提取：

```python
def _set_task_status(task: Task, next_status: str, now: datetime) -> None:
    """维护单个任务的状态、完成时间和更新时间不变量。"""
    if next_status == TaskStatus.COMPLETED.value:
        if task.status != TaskStatus.COMPLETED.value:
            task.completed_at = now
    else:
        task.completed_at = None
    task.status = next_status
    task.updated_at = now


def _cancel_unfinished_children(
    session: Session,
    user_id: str,
    parent_id: str,
    now: datetime,
) -> None:
    """在父任务事务内只级联仍未完成的当前账号直接子任务。"""
    children = session.scalars(
        select(Task).where(
            Task.user_id == user_id,
            Task.parent_id == parent_id,
            Task.status.in_((
                TaskStatus.NEW.value,
                TaskStatus.IN_PROGRESS.value,
            )),
        )
    ).all()
    for child in children:
        _set_task_status(child, TaskStatus.CANCELLED.value, now)
```

用 `_set_task_status` 替换现有 inline `completed_at` 分支；当目标状态为
`cancelled` 时，在提交前调用 `_cancel_unfinished_children`。调用方现有
`try/except` 继续负责整体 rollback。

列表组装同时计算：

```python
completed_child_count=sum(
    child.status == TaskStatus.COMPLETED.value for child in children
),
resolved_child_count=sum(
    child.status in {
        TaskStatus.COMPLETED.value,
        TaskStatus.CANCELLED.value,
    }
    for child in children
),
```

- [ ] **Step 4: 运行 Task 2 测试并确认 GREEN**

Run:

```powershell
mise exec -- pnpm test:api -- tests/test_tasks_service.py tests/test_task_schemas.py -q
```

Expected: PASS；失败提交不留下部分级联，完整与上下文任务组统计一致。

- [ ] **Step 5: 用户明确授权后提交 Task 2**

```powershell
git add -- apps/api/app/schemas/tasks.py apps/api/app/services/tasks.py apps/api/tests/test_task_schemas.py apps/api/tests/test_tasks_service.py
git commit -m "feat(api): 原子级联废弃未完成子任务"
```

## Task 3: 同步公开与内部 HTTP 契约

**Files:**
- Modify: `apps/api/app/api/routes/tasks.py`
- Modify: `apps/api/app/api/routes/mcp_tasks.py`
- Test: `apps/api/tests/test_tasks_api.py`
- Test: `apps/api/tests/test_mcp_tasks_api.py`

- [ ] **Step 1: 写 HTTP 第四状态、筛选、级联与统计失败测试**

在公开 API 测试中覆盖：

```python
cancelled = task_client.patch(
    f"/api/v1/tasks/{parent_id}",
    json={"status": "cancelled"},
)
assert cancelled.status_code == 200
assert cancelled.json()["status"] == "cancelled"
assert cancelled.json()["completed_at"] is None

page = task_client.get("/api/v1/tasks", params={"status": "cancelled"})
assert page.status_code == 200
assert page.json()["items"][0]["resolved_child_count"] == 2
```

通过详情接口断言 `new` / `in_progress` 子任务已废弃、`completed` 子任务保持完成；
再把父任务恢复为 `new`，断言子任务不恢复。更新 OpenAPI 响应字段测试，使
`TaskGroupResponse` 包含 `resolved_child_count`。

在内部 MCP API 测试中用 `serial` 重复同一状态变更，断言 Bearer、serial、
响应状态与级联结果保持真实 HTTP 契约。

- [ ] **Step 2: 运行 HTTP 测试并确认 RED**

```powershell
mise exec -- pnpm test:api -- tests/test_tasks_api.py tests/test_mcp_tasks_api.py -q
```

Expected: FAIL，路由任务组映射缺少 `resolved_child_count`，旧 HTTP 测试未覆盖第四状态。

- [ ] **Step 3: 给两个路由映射增加统计字段**

在公开和内部 `TaskGroupResponse(...)` 中加入：

```python
resolved_child_count=group.resolved_child_count,
```

不新增状态专用路由；公开 API 继续使用任务 PATCH，内部 MCP API 继续使用 serial PATCH。

- [ ] **Step 4: 运行 Task 3 测试并确认 GREEN**

Run:

```powershell
mise exec -- pnpm test:api -- tests/test_tasks_api.py tests/test_mcp_tasks_api.py -q
```

Expected: PASS，公开与内部 HTTP 契约返回相同的四状态和统计语义。

- [ ] **Step 5: 用户明确授权后提交 Task 3**

```powershell
git add -- apps/api/app/api/routes/tasks.py apps/api/app/api/routes/mcp_tasks.py apps/api/tests/test_tasks_api.py apps/api/tests/test_mcp_tasks_api.py
git commit -m "feat(api): 暴露废弃状态与已处理统计"
```

## Task 4: 扩展 MCP 工具 schema 与输出模型

**Files:**
- Modify: `apps/mcp/app/schemas.py`
- Modify: `apps/mcp/app/tools.py`
- Test: `apps/mcp/tests/test_tools.py`
- Test: `apps/mcp/tests/test_api_client.py`

- [ ] **Step 1: 写 MCP schema 与传递失败测试**

把工具测试的状态 enum 期望改为：

```python
assert set(status_schema["$defs"]["TaskStatus"]["enum"]) == {
    "new",
    "in_progress",
    "completed",
    "cancelled",
}
```

在 `list_tasks` 输入 schema 断言 `status` 包含 `all` 和四状态；在 fake API
任务组 payload 加 `resolved_child_count`。新增调用：

```python
await client.call_tool(
    "set_task_status",
    {"serial": 9, "status": "cancelled"},
)
assert fake.calls[-1] == (
    "update_task",
    {
        "token": TOKEN,
        "request_id": REQUEST_ID,
        "serial": 9,
        "patch": {"status": "cancelled"},
    },
)
```

在 API client 测试的任务组响应加入 `resolved_child_count`，并断言
`status=cancelled` 原样传给内部 API。

- [ ] **Step 2: 运行 MCP 测试并确认 RED**

```powershell
mise exec -- pnpm test:mcp -- tests/test_tools.py tests/test_api_client.py -q
```

Expected: FAIL，Literal/StrEnum 不接受 `cancelled`，严格输出模型拒绝新统计字段。

- [ ] **Step 3: 实现 MCP 第四状态与统计字段**

在 `schemas.py` 统一改为：

```python
TaskStatusValue = Literal["new", "in_progress", "completed", "cancelled"]
TaskStatusFilter = Literal[
    "all", "new", "in_progress", "completed", "cancelled"
]


class TaskStatus(StrEnum):
    """状态写工具允许的四个 Tickly 状态。"""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
```

`TaskGroupPayload` 增加 `resolved_child_count: int`。把 `set_task_status` docstring
改为“把任务切换为 New、In Progress、Completed 或 Cancelled”，不在工具层实现级联。

- [ ] **Step 4: 运行 Task 4 测试并确认 GREEN**

Run:

```powershell
mise exec -- pnpm test:mcp -- tests/test_tools.py tests/test_api_client.py -q
```

Expected: PASS；第四状态与统计字段通过真实 MCP SDK schema 和调用测试。

- [ ] **Step 5: 用户明确授权后提交 Task 4**

```powershell
git add -- apps/mcp/app/schemas.py apps/mcp/app/tools.py apps/mcp/tests/test_tools.py apps/mcp/tests/test_api_client.py
git commit -m "feat(mcp): 支持废弃待办状态"
```

## Task 5: 扩展 Web 数据契约、URL 恢复和局部统计

**Files:**
- Modify: `apps/web/src/features/tasks/task-api.ts`
- Modify: `apps/web/src/features/tasks/use-task-workspace.ts`
- Test: `apps/web/src/features/tasks/task-api.test.ts`
- Test: `apps/web/src/features/tasks/use-task-workspace.test.tsx`

- [ ] **Step 1: 写 Web 类型行为与状态刷新失败测试**

更新所有测试任务组 fixture，新增：

```typescript
resolved_child_count: children.filter((child) =>
  ["completed", "cancelled"].includes(child.status)
).length,
```

新增测试：

```typescript
it("从 URL 恢复 cancelled 状态并按废弃筛选请求", async () => {
  window.history.replaceState({}, "", "/?status=cancelled")
  const { result } = renderHook(() => useTaskWorkspace())
  await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
  expect(result.current.state.query.status).toBe("cancelled")
  expect(tasks.listTasks).toHaveBeenCalledWith(
    expect.objectContaining({ status: "cancelled" }),
    expect.any(AbortSignal)
  )
})
```

扩展状态 mutation 测试：废弃子任务时乐观状态为 `cancelled`、
`completed_at` 为 `null`，服务端刷新返回的 `resolved_child_count` 覆盖客户端；
失败时恢复旧状态和两种计数。

- [ ] **Step 2: 运行 Web 数据层测试并确认 RED**

```powershell
mise exec -- pnpm test:web -- task-api.test.ts use-task-workspace.test.tsx
```

Expected: FAIL，TypeScript 状态 union、URL 白名单和任务组字段缺少新增值。

- [ ] **Step 3: 实现 Web 类型、URL 与局部统计**

在 `task-api.ts` 修改：

```typescript
export type TaskStatus = "new" | "in_progress" | "completed" | "cancelled"
export type TaskStatusFilter = "all" | TaskStatus

export type TaskGroup = {
  task: Task
  children: Task[]
  child_count: number
  completed_child_count: number
  resolved_child_count: number
  context_only: boolean
}
```

URL 白名单加入 `cancelled`。使用明确 helper，避免 boolean 与数字混算：

```typescript
function isResolvedStatus(status: TaskStatus): boolean {
  return status === "completed" || status === "cancelled"
}

resolved_child_count:
  group.resolved_child_count + (isResolvedStatus(child.status) ? 1 : 0),
```

`updateTaskInGroups` 对完整 `nextChildren` 同时重算 `completed_child_count` 和
`resolved_child_count`。乐观任务继续只把 `completed` 保留原完成时间，其他状态置 `null`；级联子任务不在客户端猜测，等待 `loadFirstPage`。

- [ ] **Step 4: 运行 Task 5 测试并确认 GREEN**

Run:

```powershell
mise exec -- pnpm test:web -- task-api.test.ts use-task-workspace.test.tsx
```

Expected: PASS，URL、乐观写入、回滚和服务端统计覆盖均支持第四状态。

- [ ] **Step 5: 用户明确授权后提交 Task 5**

```powershell
git add -- apps/web/src/features/tasks/task-api.ts apps/web/src/features/tasks/use-task-workspace.ts apps/web/src/features/tasks/task-api.test.ts apps/web/src/features/tasks/use-task-workspace.test.tsx
git commit -m "feat(web): 接入待办废弃状态数据契约"
```

## Task 6: 完成 Web 筛选、状态控件和视觉反馈

**Files:**
- Modify: `apps/web/src/features/tasks/task-filter-controls.tsx`
- Modify: `apps/web/src/features/tasks/todo-workspace.tsx`
- Modify: `apps/web/src/features/tasks/task-list.tsx`
- Modify: `apps/web/src/features/tasks/task-group.tsx`
- Modify: `apps/web/src/features/tasks/task-row.tsx`
- Modify: `apps/web/src/features/tasks/task-editor-panel.tsx`
- Modify: `apps/web/src/index.css`
- Test: `apps/web/src/features/tasks/task-table.test.tsx`
- Test: `apps/web/src/features/tasks/task-editor-panel.test.tsx`
- Test: `apps/web/src/features/tasks/todo-workspace.test.tsx`

- [ ] **Step 1: 写 UI 失败测试**

新增或更新断言：

```typescript
expect(screen.getByRole("button", { name: "Cancelled" })).toBeInTheDocument()
expect(screen.getByText("2/3 已处理")).toBeInTheDocument()
await user.selectOptions(screen.getByLabelText("状态"), "cancelled")
expect(onSave).toHaveBeenCalledWith(
  expect.any(String),
  expect.objectContaining({ status: "cancelled" })
)
```

在参数化空状态测试加入：

```typescript
["cancelled", "还没有已废弃的任务。"],
```

行表测试选择 `设置 #12 的状态` 为 `cancelled`，断言
`onStatusChange(task, "cancelled")`。对废弃行断言 `data-status="cancelled"`，
标题存在独立弱化 class/style，而不是完成时间或删除提示。

- [ ] **Step 2: 运行 UI 测试并确认 RED**

```powershell
mise exec -- pnpm test:web -- task-table.test.tsx task-editor-panel.test.tsx todo-workspace.test.tsx
```

Expected: FAIL，筛选、状态选项、空文案和进度仍只有三状态/“已完成”。

- [ ] **Step 3: 实现 UI 第四状态**

各状态表加入：

```typescript
{ value: "cancelled", label: "Cancelled" }
```

编辑面板加入：

```tsx
<option value="cancelled">已废弃</option>
```

`statusLabels` 增加 `cancelled: "Cancelled"`；`emptyMessages` 增加
`cancelled: "还没有已废弃的任务。"`。`TaskGroupView` 传递：

```tsx
progress={
  group.child_count > 0
    ? { resolved: group.resolved_child_count, total: group.child_count }
    : undefined
}
```

`TaskRow` 的 progress 类型改为 `{ resolved: number; total: number }`，展示：

```tsx
<span className="task-row-progress">
  {progress.resolved}/{progress.total} 已处理
</span>
```

保留完成任务删除线；废弃任务增加弱化但可读的独立样式：

```css
.task-row[data-status="cancelled"] .task-row-title {
  color: var(--muted-foreground);
  text-decoration: line-through;
  text-decoration-style: dashed;
}
```

不要隐藏废弃行，也不要在编辑面板显示不存在的废弃时间。

- [ ] **Step 4: 运行 Task 6 测试并确认 GREEN**

Run:

```powershell
mise exec -- pnpm test:web -- task-table.test.tsx task-editor-panel.test.tsx todo-workspace.test.tsx
```

Expected: PASS，桌面/移动筛选、编辑、行内切换、空态与已处理进度一致。

- [ ] **Step 5: 用户明确授权后提交 Task 6**

```powershell
git add -- apps/web/src/features/tasks/task-filter-controls.tsx apps/web/src/features/tasks/todo-workspace.tsx apps/web/src/features/tasks/task-list.tsx apps/web/src/features/tasks/task-group.tsx apps/web/src/features/tasks/task-row.tsx apps/web/src/features/tasks/task-editor-panel.tsx apps/web/src/index.css apps/web/src/features/tasks/task-table.test.tsx apps/web/src/features/tasks/task-editor-panel.test.tsx apps/web/src/features/tasks/todo-workspace.test.tsx
git commit -m "feat(web): 展示并筛选已废弃待办"
```

## Task 7: 完善 Tickly MCP Skill 和当前事实文档

**Files:**
- Modify: `skills/tickly-todo-mcp/evals/evals.json`
- Modify: `skills/tickly-todo-mcp/SKILL.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/mcp.md`

- [ ] **Step 1: 先增加 Skill 行为 eval**

在 `evals.json` 增加三个场景，保持 ID 唯一：

```json
{
  "id": 5,
  "prompt": "废弃父任务 #42。它有 #43(new)、#44(in_progress)、#45(completed) 三个直接子任务。",
  "expected_output": "先用 get_task 核验并明确提示 #43、#44 会被级联废弃，#45 保持完成，恢复 #42 不会恢复子任务；用户确认后才调用 set_task_status，并在写入后重新读取报告真实结果。",
  "files": []
},
{
  "id": 6,
  "prompt": "废弃父任务 #42。看到影响提示后，我说不执行。",
  "expected_output": "停止操作，不调用任何写工具，并明确说明未修改任务。",
  "files": []
},
{
  "id": 7,
  "prompt": "把已废弃的父任务 #42 恢复为 New。",
  "expected_output": "提示恢复父任务不会自动恢复子任务，确认后只修改 #42，并重新读取核验。",
  "files": []
}
```

- [ ] **Step 2: 运行 Skill RED 语义检查**

用 PowerShell 读取 eval 与 Skill，断言 Skill 包含以下短语：

```text
cancelled
恢复父任务不会自动恢复子任务
保持完成
再次调用 get_task
```

Expected: FAIL，当前 Skill 未定义废弃状态和父子提示门。

- [ ] **Step 3: 修改 Skill 与当前事实文档**

在 `SKILL.md` 的核心约束和创建/更新流程中写明：

```markdown
- `cancelled` 表示可恢复的“已废弃”，不是删除；进入该状态会清空完成时间。
- 废弃父任务前先调用 `get_task`：列出会级联废弃的 `new` / `in_progress`
  子任务 serial、保持不变的 `completed` 子任务 serial，并提示恢复父任务不会
  自动恢复子任务。用户确认后才调用 `set_task_status`。
- 写入后再次调用 `get_task`，按实际状态报告父任务、级联子任务和保持完成项；
  不得把预期级联描述成已经成功。
```

更新：

- `AGENTS.md`：Web/API 当前能力从三状态改为四状态并说明父任务废弃级联。
- `README.md`：当前状态列出 `cancelled`。
- `docs/mcp.md`：`list_tasks`/`set_task_status` 支持第四状态，并说明服务端级联与 AI 确认边界。

不要修改历史 `docs/superpowers/specs`、`plans` 或 roadmap 中对当时三状态的记录。

- [ ] **Step 4: 验证 Skill 与文档**

```powershell
$env:PYTHONUTF8 = '1'
$env:UV_CACHE_DIR = 'D:\GitHub\tickly\.uv-cache'
mise exec -- uv run --with pyyaml C:\Users\Potato\.agents\skills\skill-creator\scripts\quick_validate.py D:\GitHub\tickly\skills\tickly-todo-mcp
git diff --check -- skills/tickly-todo-mcp AGENTS.md README.md docs/mcp.md
```

Expected: `Skill is valid!`，7 个 eval ID 唯一，diff check 无错误。

- [ ] **Step 5: 用户明确授权后提交 Task 7**

```powershell
git add -- skills/tickly-todo-mcp/SKILL.md skills/tickly-todo-mcp/evals/evals.json AGENTS.md README.md docs/mcp.md
git commit -m "docs(skills): 明确待办废弃操作边界"
```

## Task 8: 全链路回归、差异审计和知识判断

**Files:**
- Verify: 本计划涉及的全部文件
- Optional Modify: `.agents/knowledge/apps-web-todo-tree-mutations.md`（仅在当前代码证明新增稳定联动且满足知识准入时）

- [ ] **Step 1: 运行 API 与 MCP 契约测试**

```powershell
mise exec -- pnpm test:api
mise exec -- pnpm test:mcp
```

Expected: 全部通过；重点核对 Bearer、`serial`、第四状态、`resolved_child_count`、稳定错误码和请求 ID。

- [ ] **Step 2: 运行 Web 全量验证**

```powershell
mise exec -- pnpm test:web
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
```

Expected: Vitest、ESLint、TypeScript 和生产构建全部通过。

- [ ] **Step 3: 验证 migration 真实文件数据库往返**

依赖 `test_migrations.py` 的临时 SQLite 文件用例，再单独运行：

```powershell
mise exec -- pnpm test:api -- tests/test_migrations.py -q
```

Expected: `0002 -> 0003 -> 0002` 往返通过，废弃任务降级为 `new`。

- [ ] **Step 4: 审计最终差异与工作树边界**

```powershell
git diff --check
git diff --stat
git status --short
```

Expected: 只有本计划文件和用户已有的无关改动；不得纳入 `apps/.pytest-release/` 或其他未授权文件。

- [ ] **Step 5: 按仓库知识准入标准处理稳定增量**

用当前实现和测试核实 `.agents/knowledge/apps-web-todo-tree-mutations.md`。只有当
“服务端父任务废弃级联 + `resolved_child_count` 完整集合统计 + Web 不猜测级联”
构成稳定、非显然、会影响未来状态修改的联动时，才最小更新该卡及索引；否则
不修改知识文件。

- [ ] **Step 6: 用户明确授权后创建最终提交并按要求推送**

先用精确路径暂存并检查：

```powershell
git diff --name-only
git add -- apps/api/alembic/versions/0003_add_cancelled_task_status.py apps/api/app/models/task.py apps/api/app/schemas/tasks.py apps/api/app/services/tasks.py apps/api/app/api/routes/tasks.py apps/api/app/api/routes/mcp_tasks.py apps/api/tests/test_migrations.py apps/api/tests/test_models.py apps/api/tests/test_task_schemas.py apps/api/tests/test_tasks_service.py apps/api/tests/test_tasks_api.py apps/api/tests/test_mcp_tasks_api.py
git add -- apps/mcp/app/schemas.py apps/mcp/app/tools.py apps/mcp/tests/test_tools.py apps/mcp/tests/test_api_client.py
git add -- apps/web/src/features/tasks/task-api.ts apps/web/src/features/tasks/use-task-workspace.ts apps/web/src/features/tasks/task-filter-controls.tsx apps/web/src/features/tasks/todo-workspace.tsx apps/web/src/features/tasks/task-list.tsx apps/web/src/features/tasks/task-group.tsx apps/web/src/features/tasks/task-row.tsx apps/web/src/features/tasks/task-editor-panel.tsx apps/web/src/index.css apps/web/src/features/tasks/task-api.test.ts apps/web/src/features/tasks/use-task-workspace.test.tsx apps/web/src/features/tasks/task-table.test.tsx apps/web/src/features/tasks/task-editor-panel.test.tsx apps/web/src/features/tasks/todo-workspace.test.tsx
git add -- skills/tickly-todo-mcp/SKILL.md skills/tickly-todo-mcp/evals/evals.json AGENTS.md README.md docs/mcp.md docs/superpowers/specs/2026-08-24-todo-cancelled-status-design.md docs/superpowers/plans/2026-08-24-todo-cancelled-status.md
git diff --cached --name-status
git diff --cached --check
git commit -m "feat(tasks): 增加可恢复的废弃状态"
```

只有用户明确要求推送时再执行：

```powershell
git push origin main
```
