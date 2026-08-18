# Todo List Web Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Todo Web 调整为桌面两栏、移动筛选抽屉的响应式工作区，并完整支持新版任务字段、三状态和一层父子任务树。

**Architecture:** `TodoWorkspace` 保持唯一业务容器，`useTaskWorkspace` 管理服务端树分组、筛选 query 和 mutation；筛选控件拆成桌面侧栏与移动 Dialog 复用的纯组件。列表直接渲染 API 返回的 `TaskGroup`，稳定 ID 作为 React key，编辑面板继续通过任务 ID key 重置独立表单快照。

**Tech Stack:** React 19.2、TypeScript 6、Vite 8、Tailwind CSS 4、Base UI Dialog、Vitest、Testing Library

---

## 前置条件与执行约束

- 先完成并验证 `docs/superpowers/plans/2026-08-17-todo-task-model-api.md`。
- 从仓库根目录运行命令。
- 复用现有 React、Tailwind、Base UI、Lucide、`Button` 和 `apiFetch`，不新增依赖。
- 不引入路由器、全局 Store、TanStack Query、表单库或 `packages/*`。
- access token 和任务 query 不写入任何浏览器持久化存储。
- 按 TDD 顺序执行；每个代码任务先确认测试失败再实现。
- 提交步骤只有在用户明确要求“提交”时才能执行。
- 不处理工作区已有的 SQLite `-shm`/`-wal` 文件。

## 文件结构

### 新建

- `apps/web/src/features/tasks/task-filter-controls.tsx`：桌面和移动共同使用的状态、主题和排序控件。
- `apps/web/src/features/tasks/task-filter-sidebar.tsx`：桌面固定筛选栏及主题加载错误。
- `apps/web/src/features/tasks/mobile-task-filter-dialog.tsx`：移动筛选草稿与一次性应用。
- `apps/web/src/features/tasks/task-group.tsx`：父任务、子任务列表和完成进度。
- `apps/web/src/features/tasks/parent-task-picker.tsx`：按 serial/标题查询父任务候选。
- `apps/web/src/features/tasks/child-task-create-form.tsx`：在父任务编辑面板内创建子待办。

### 修改

- `apps/web/src/features/tasks/task-api.ts`：新版 DTO、tree/topic/parent-options API。
- `apps/web/src/features/tasks/task-api.test.ts`：请求路径、query 和请求体契约。
- `apps/web/src/features/tasks/use-task-workspace.ts`：树分页、主题状态和三状态 mutation。
- `apps/web/src/features/tasks/use-task-workspace.test.tsx`：hook 请求竞争、树合并和回滚。
- `apps/web/src/features/tasks/todo-workspace.tsx`：两栏组合和选中任务定位。
- `apps/web/src/features/tasks/todo-workspace.test.tsx`：桌面语义、移动筛选、树交互和创建。
- `apps/web/src/features/tasks/task-toolbar.tsx`：删除；职责由新筛选组件替代。
- `apps/web/src/features/tasks/quick-create-form.tsx`：标题与必填主题。
- `apps/web/src/features/tasks/task-list.tsx`：接收 `TaskGroup[]`。
- `apps/web/src/features/tasks/task-row.tsx`：serial、主题、三状态选择和时间元数据。
- `apps/web/src/features/tasks/task-time.ts`：保留截止时间语义并格式化创建/完成时间。
- `apps/web/src/features/tasks/task-editor-panel.tsx`：描述、主题、状态、父级和子待办编辑。
- `apps/web/src/features/tasks/task-editor-panel.test.tsx`：最小 PATCH 和必填字段。
- `apps/web/src/features/tasks/delete-task-dialog.tsx`：父任务删除提升提示。
- `apps/web/src/index.css`：两栏、筛选、任务树和移动断点样式。
- `README.md`：更新当前 Todo 能力和 API 路径。
- `AGENTS.md`：更新真实的 Web/API 当前状态。
- `docs/roadmaps/2026-07-26-tickly-zero-to-one.md`：校准任务 schema、筛选状态和子任务范围。

## Task 1: 更新 Web API 类型与三个读取接口

**Files:**
- Modify: `apps/web/src/features/tasks/task-api.ts`
- Modify: `apps/web/src/features/tasks/task-api.test.ts`

- [ ] **Step 1: 先把测试 fixture 改成新版 Task**

```typescript
const task: Task = {
  id: "task-id",
  serial: 18,
  title: "阶段 4",
  description: "阶段 4",
  priority: null,
  topic: "Tickly",
  status: "new",
  due_at: null,
  completed_at: null,
  parent_id: null,
  created_at: "2026-07-28T08:00:00Z",
  updated_at: "2026-07-28T08:00:00Z",
}

const group: TaskGroup = {
  task,
  children: [],
  child_count: 0,
  completed_child_count: 0,
  context_only: false,
}
```

列表测试 query 改为：

```typescript
const query: TaskListQuery = {
  status: "in_progress",
  topic: "Tickly",
  sort: "serial",
  order: "asc",
  limit: 50,
  cursor: "next/+==",
}
```

预期 URL 必须包含编码后的 `topic`，响应 items 为 `[group]`。

- [ ] **Step 2: 增加 topics 和 parent-options 客户端测试**

```typescript
it("读取主题和分页父待办候选", async () => {
  api.apiFetch
    .mockResolvedValueOnce(jsonResponse({ items: ["Tickly", "工作"] }))
    .mockResolvedValueOnce(
      jsonResponse({
        items: [
          {
            id: "parent-id",
            serial: 7,
            title: "父任务",
            topic: "Tickly",
            status: "in_progress",
          },
        ],
        next_cursor: "parent-next",
      }),
    )

  await expect(listTaskTopics()).resolves.toEqual(["Tickly", "工作"])
  await expect(
    listParentOptions({ query: "#7", limit: 20 }),
  ).resolves.toEqual(expect.objectContaining({ next_cursor: "parent-next" }))

  expect(api.apiFetch).toHaveBeenNthCalledWith(1, "/api/v1/tasks/topics")
  expect(api.apiFetch).toHaveBeenNthCalledWith(
    2,
    "/api/v1/tasks/parent-options?limit=20&query=%237",
    { signal: undefined },
  )
})
```

创建/更新测试必须断言：

```typescript
await createTask({ title: "阶段 4", topic: "Tickly" })
await updateTask("task/id", {
  description: "详细说明",
  priority: null,
  due_at: null,
})
```

- [ ] **Step 3: 运行客户端测试确认旧类型失败**

Run:

```powershell
mise exec -- pnpm test:web -- task-api.test.ts
```

Expected: FAIL，TypeScript 报告 `serial`、`TaskGroup` 或新函数不存在。

- [ ] **Step 4: 替换 task-api 类型**

```typescript
export type TaskPriority = "low" | "medium" | "high"
export type TaskStatus = "new" | "in_progress" | "completed"
export type TaskStatusFilter = "all" | TaskStatus
export type TaskSort = "serial" | "created_at" | "due_at" | "priority"
export type SortOrder = "asc" | "desc"

export type Task = {
  id: string
  serial: number
  title: string
  description: string
  priority: TaskPriority | null
  topic: string
  status: TaskStatus
  due_at: string | null
  completed_at: string | null
  parent_id: string | null
  created_at: string
  updated_at: string
}

export type TaskGroup = {
  task: Task
  children: Task[]
  child_count: number
  completed_child_count: number
  context_only: boolean
}

export type TaskPage = {
  items: TaskGroup[]
  next_cursor: string | null
}

export type TaskListQuery = {
  status: TaskStatusFilter
  topic?: string
  sort: TaskSort
  order: SortOrder
  limit: number
  cursor?: string
}

export type TaskCreateInput = {
  title: string
  description?: string | null
  priority?: TaskPriority | null
  topic: string
  due_at?: string | null
  parent_id?: string | null
}

export type TaskUpdateInput = Partial<
  Pick<
    Task,
    "title" | "description" | "priority" | "topic" | "status" | "due_at" | "parent_id"
  >
>

export type ParentTaskOption = Pick<
  Task,
  "id" | "serial" | "title" | "topic" | "status"
>

export type ParentOptionQuery = {
  query?: string
  cursor?: string
  limit: number
}

export type ParentOptionPage = {
  items: ParentTaskOption[]
  next_cursor: string | null
}
```

默认 query：

```typescript
export const DEFAULT_TASK_QUERY = {
  status: "all",
  sort: "created_at",
  order: "desc",
  limit: 50,
} satisfies TaskListQuery
```

- [ ] **Step 5: 实现新 query 和读取函数**

`listTasks` 只在 topic 有值时附加参数：

```typescript
if (query.topic !== undefined) {
  params.set("topic", query.topic)
}
```

新增：

```typescript
export async function listTaskTopics(): Promise<string[]> {
  const response = await apiFetch("/api/v1/tasks/topics")
  const body = await readJson<{ items: string[] }>(response)
  return body.items
}

export async function listParentOptions(
  query: ParentOptionQuery,
  signal?: AbortSignal,
): Promise<ParentOptionPage> {
  const params = new URLSearchParams({ limit: String(query.limit) })
  if (query.query !== undefined) params.set("query", query.query)
  if (query.cursor !== undefined) params.set("cursor", query.cursor)
  const response = await apiFetch(
    `/api/v1/tasks/parent-options?${params.toString()}`,
    { signal },
  )
  return readJson<ParentOptionPage>(response)
}
```

- [ ] **Step 6: 运行 API 客户端测试**

Run:

```powershell
mise exec -- pnpm test:web -- task-api.test.ts
```

Expected: PASS。

- [ ] **Step 7: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/task-api.ts apps/web/src/features/tasks/task-api.test.ts
git commit -m "feat(web): 接入新版待办树契约"
```

## Task 2: 将 workspace 状态改为任务分组和主题筛选

**Files:**
- Modify: `apps/web/src/features/tasks/use-task-workspace.ts`
- Modify: `apps/web/src/features/tasks/use-task-workspace.test.tsx`

- [ ] **Step 1: 写分组分页、主题加载和 query 竞争测试**

测试 fixture：

```typescript
function makeTask(id: string, serial: number, status: TaskStatus = "new"): Task {
  return {
    id,
    serial,
    title: `任务 ${id}`,
    description: `任务 ${id}`,
    priority: null,
    topic: "Tickly",
    status,
    due_at: null,
    completed_at: null,
    parent_id: null,
    created_at: "2026-08-17T08:00:00Z",
    updated_at: "2026-08-17T08:00:00Z",
  }
}

function makeGroup(task: Task, children: Task[] = []): TaskGroup {
  return {
    task,
    children,
    child_count: children.length,
    completed_child_count: children.filter(
      (child) => child.status === "completed",
    ).length,
    context_only: false,
  }
}
```

新增断言：

- mount 同时调用 `listTasks(DEFAULT_TASK_QUERY)` 和 `listTaskTopics()`。
- `setTopic("Tickly")` 取消旧列表请求并发起带 topic 的第一页。
- 加载更多按根任务 ID 去重，不按子任务 ID 合并分组。
- 主题读取失败只设置 `topicError`，不覆盖列表错误。
- 创建成功后刷新列表和主题；创建失败保留调用方表单输入。

- [ ] **Step 2: 写三状态乐观更新与回滚测试**

测试父任务和子任务各一条：

```typescript
await act(async () => {
  statusPromise = result.current.actions.changeStatus(child, "completed")
})
expect(result.current.state.items[0].children[0].status).toBe("completed")

await act(async () => {
  updateDeferred.reject(new Error("网络中断"))
  await expect(statusPromise).rejects.toThrow("网络中断")
})
expect(result.current.state.items[0].children[0].status).toBe("new")
expect(result.current.state.statusError).toBe("任务状态更新失败")
```

成功时先使用服务端响应替换对应节点，再调用 `loadFirstPage`，保证筛选和 `context_only` 由服务端重新计算。

- [ ] **Step 3: 运行 hook 测试确认旧状态失败**

Run:

```powershell
mise exec -- pnpm test:web -- use-task-workspace.test.tsx
```

Expected: FAIL，旧 state 使用 `Task[]`、没有 topic 状态或 `changeStatus`。

- [ ] **Step 4: 定义新的 workspace state 和树辅助函数**

```typescript
type WorkspaceQuery = Omit<TaskListQuery, "cursor">

export type TaskWorkspaceState = {
  query: WorkspaceQuery
  items: TaskGroup[]
  topics: string[]
  topicLoading: boolean
  topicError: string | null
  nextCursor: string | null
  initialLoading: boolean
  loadingMore: boolean
  error: string | null
  selectedTaskId: string | null
  creating: boolean
  saving: boolean
  deleting: boolean
  statusMutatingTaskIds: ReadonlySet<string>
  statusError: string | null
}

function appendUniqueGroups(
  current: TaskGroup[],
  incoming: TaskGroup[],
): TaskGroup[] {
  const known = new Set(current.map((group) => group.task.id))
  return current.concat(
    incoming.filter((group) => !known.has(group.task.id)),
  )
}

export function findTaskInGroups(
  groups: TaskGroup[],
  taskId: string,
): Task | null {
  for (const group of groups) {
    if (group.task.id === taskId) return group.task
    const child = group.children.find((task) => task.id === taskId)
    if (child !== undefined) return child
  }
  return null
}

function replaceTaskInGroups(
  groups: TaskGroup[],
  task: Task,
): TaskGroup[] {
  return groups.map((group) => ({
    ...group,
    task: group.task.id === task.id ? task : group.task,
    children: group.children.map((child) =>
      child.id === task.id ? task : child,
    ),
  }))
}
```

- [ ] **Step 5: 更新 query、topics、创建和状态 action**

`updateQuery` 比较逻辑加入 topic：

```typescript
next.topic === queryRef.current.topic
```

actions 签名：

```typescript
export type TaskWorkspaceActions = {
  setStatus(status: TaskStatusFilter): void
  setTopic(topic: string | undefined): void
  setSort(sort: TaskSort): void
  setOrder(order: SortOrder): void
  applyQuery(query: WorkspaceQuery): void
  retry(): Promise<void>
  retryTopics(): Promise<void>
  loadMore(): Promise<void>
  selectTask(taskId: string | null): void
  create(input: TaskCreateInput): Promise<void>
  save(taskId: string, patch: TaskUpdateInput): Promise<void>
  changeStatus(task: Task, status: TaskStatus): Promise<void>
  remove(taskId: string): Promise<void>
}
```

主题读取保持独立错误域：

```typescript
const loadTopics = useCallback(async () => {
  setState((current) => ({ ...current, topicLoading: true, topicError: null }))
  try {
    const topics = await listTaskTopics()
    setState((current) => ({
      ...current,
      topics,
      topicLoading: false,
      topicError: null,
    }))
  } catch (error) {
    setState((current) => ({
      ...current,
      topicLoading: false,
      topicError: safeErrorMessage(error, "主题加载失败"),
    }))
  }
}, [])
```

状态更新先用快照替换树中对应节点，失败恢复，成功后重读当前 query：

```typescript
const optimisticTask: Task = {
  ...task,
  status,
  completed_at: status === "completed" ? task.completed_at : null,
}
```

不要在客户端猜测新的 `completed_at`；服务端响应返回后再替换真实值。

- [ ] **Step 6: 删除后重读分组而不是只过滤根数组**

删除父任务会提升子任务，因此 `remove` 成功后必须关闭编辑器并执行：

```typescript
await loadFirstPage(queryRef.current)
await loadTopics()
```

删除子任务也使用相同重读路径，避免本地子任务计数与服务端不一致。

- [ ] **Step 7: 运行 hook 测试**

Run:

```powershell
mise exec -- pnpm test:web -- use-task-workspace.test.tsx
```

Expected: PASS。

- [ ] **Step 8: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/use-task-workspace.ts apps/web/src/features/tasks/use-task-workspace.test.tsx
git commit -m "feat(web): 管理待办树与主题筛选状态"
```

## Task 3: 实现桌面筛选栏与移动筛选抽屉

**Files:**
- Create: `apps/web/src/features/tasks/task-filter-controls.tsx`
- Create: `apps/web/src/features/tasks/task-filter-sidebar.tsx`
- Create: `apps/web/src/features/tasks/mobile-task-filter-dialog.tsx`
- Modify: `apps/web/src/features/tasks/todo-workspace.test.tsx`

- [ ] **Step 1: 写筛选语义和移动草稿失败测试**

在 workspace 测试中 mock `listTaskTopics`，并断言：

```typescript
expect(await screen.findByRole("complementary", { name: "任务筛选" }))
  .toBeInTheDocument()
expect(screen.getByRole("main", { name: "Todo List" })).toBeInTheDocument()
expect(screen.getByRole("button", { name: "New" })).toHaveAttribute(
  "aria-pressed",
  "false",
)
```

移动抽屉测试：打开“筛选”，选择 `In Progress` 和 `Tickly` 后，关闭前不调用新列表；点击“应用筛选”后只发起一次带两个条件的列表请求。

- [ ] **Step 2: 运行 workspace 测试确认组件不存在**

Run:

```powershell
mise exec -- pnpm test:web -- todo-workspace.test.tsx
```

Expected: FAIL，找不到 complementary 区域和筛选按钮。

- [ ] **Step 3: 创建共享 TaskFilterControls**

```typescript
type TaskFilterControlsProps = {
  query: Omit<TaskListQuery, "cursor">
  topics: string[]
  disabled: boolean
  onStatusChange(status: TaskStatusFilter): void
  onTopicChange(topic: string | undefined): void
  onSortChange(sort: TaskSort): void
  onOrderChange(order: SortOrder): void
}
```

状态按钮固定为：

```typescript
const statusOptions: Array<{ value: TaskStatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "new", label: "New" },
  { value: "in_progress", label: "In Progress" },
  { value: "completed", label: "Completed" },
]
```

主题使用单选按钮，“全部主题”传 `undefined`；排序 select 使用 `serial`、`created_at`、`due_at`、`priority`，顺序 select 保持升降序。

- [ ] **Step 4: 创建桌面侧栏**

`TaskFilterSidebar` 使用：

```tsx
<aside className="task-filter-sidebar" aria-label="任务筛选">
  <div className="task-filter-sidebar-inner">
    <TaskFilterControls {...controlProps} />
    {topicError !== null ? (
      <div className="task-filter-error">
        <p role="alert">{topicError}</p>
        <Button type="button" variant="outline" onClick={() => void onRetryTopics()}>
          重试主题
        </Button>
      </div>
    ) : null}
    <Button type="button" variant="ghost" onClick={onReset}>
      清除筛选
    </Button>
  </div>
</aside>
```

清除筛选恢复 `DEFAULT_TASK_QUERY`，不是清除任务数据。

- [ ] **Step 5: 创建移动筛选 Dialog**

外层 Dialog 管理 open，内部表单用 key 重置草稿：

```tsx
{open ? (
  <MobileFilterForm
    key={`${query.status}:${query.topic ?? "all"}:${query.sort}:${query.order}`}
    query={query}
    topics={topics}
    onApply={(draft) => {
      onApply(draft)
      setOpen(false)
    }}
  />
) : null}
```

`MobileFilterForm` 使用 `useState(query)` 持有草稿；只有“应用筛选”调用父级 `onApply`。Dialog 标题为“筛选与排序”，支持 Escape 和关闭按钮。

- [ ] **Step 6: 运行筛选交互测试**

Run:

```powershell
mise exec -- pnpm test:web -- todo-workspace.test.tsx
```

Expected: 新筛选测试通过；尚未改造的旧列表 fixture 可以失败在 Task 4 的树响应结构。

- [ ] **Step 7: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/task-filter-controls.tsx apps/web/src/features/tasks/task-filter-sidebar.tsx apps/web/src/features/tasks/mobile-task-filter-dialog.tsx apps/web/src/features/tasks/todo-workspace.test.tsx
git commit -m "feat(web): 增加响应式待办筛选器"
```

## Task 4: 实现必填主题快速创建和任务树列表

**Files:**
- Create: `apps/web/src/features/tasks/task-group.tsx`
- Modify: `apps/web/src/features/tasks/quick-create-form.tsx`
- Modify: `apps/web/src/features/tasks/task-list.tsx`
- Modify: `apps/web/src/features/tasks/task-row.tsx`
- Modify: `apps/web/src/features/tasks/task-time.ts`
- Modify: `apps/web/src/features/tasks/task-time.test.ts`
- Modify: `apps/web/src/features/tasks/todo-workspace.test.tsx`

- [ ] **Step 1: 写快速创建与树展示失败测试**

快速创建断言：

- “全部主题”下标题和主题都必填。
- 当前 query topic 为 `Tickly` 时，主题输入初始值为 `Tickly`，但允许改成“工作”。
- 成功请求为 `{ title: "阶段 4", topic: "Tickly" }`。
- 失败时标题和主题都保留。

树展示断言：

```typescript
expect(screen.getByText("#18")).toBeInTheDocument()
expect(screen.getByText("子待办 1/2 已完成")).toBeInTheDocument()
expect(screen.getByRole("list", { name: "#18 的子待办" }))
  .toBeInTheDocument()
expect(screen.getByRole("combobox", { name: "设置 #19 的状态" }))
  .toHaveValue("completed")
```

- [ ] **Step 2: 运行测试确认旧组件失败**

Run:

```powershell
mise exec -- pnpm test:web -- todo-workspace.test.tsx
```

Expected: FAIL，快速创建没有主题，列表仍只接收平坦 tasks。

- [ ] **Step 3: 改造 QuickCreateForm**

props：

```typescript
type QuickCreateFormProps = {
  creating: boolean
  selectedTopic?: string
  topicOptions: string[]
  onCreate(input: TaskCreateInput): Promise<void>
}
```

组件持有 `title` 和 `topic`；`selectedTopic` 改变时只在当前 topic 为空或仍等于上一个选中主题时同步，避免覆盖用户正在输入的主题。提交规则：

```typescript
const normalizedTitle = title.trim()
const normalizedTopic = topic.trim()
if (normalizedTitle === "" || normalizedTopic === "" || creating) return
await onCreate({ title: normalizedTitle, topic: normalizedTopic })
```

主题输入使用 `list="task-topic-options"` 提供服务端建议，但仍是自由文本；成功后清空标题，主题保留为当前选择或本次提交值。

- [ ] **Step 4: 改造 TaskRow 为 serial 和三状态选择**

先在 `task-time.test.ts` 增加账号时区格式化断言：

```typescript
expect(
  formatTaskTimestamp(
    "2026-08-17T08:30:00Z",
    "Asia/Shanghai",
    "创建",
  ),
).toBe("创建 · 8月17日 16:30")
```

在 `task-time.ts` 增加通用只读时间格式化函数，不复用带“逾期/今天”判断的 `formatDueLabel`：

```typescript
export function formatTaskTimestamp(
  value: string,
  timeZone: string,
  label: "创建" | "完成",
): string {
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
  return `${label} · ${formatter.format(new Date(value))}`
}
```

```typescript
type TaskRowProps = {
  task: Task
  timeZone: string
  statusMutating: boolean
  contextOnly?: boolean
  onSelect(task: Task): void
  onStatusChange(task: Task, status: TaskStatus): Promise<void>
}
```

任务行必须包含：

```tsx
<span className="task-row-serial">#{task.serial}</span>
<span className="task-row-title">{task.title}</span>
<span className="task-topic">{task.topic}</span>
<select
  aria-label={`设置 #${task.serial} 的状态`}
  value={task.status}
  disabled={statusMutating}
  onChange={(event) => {
    void onStatusChange(task, event.target.value as TaskStatus).catch(
      () => undefined,
    )
  }}
>
  <option value="new">New</option>
  <option value="in_progress">In Progress</option>
  <option value="completed">Completed</option>
</select>
```

只有 `status=completed` 使用删除线。`due_at` 继续使用 `formatDueLabel`，`created_at` 和 `completed_at` 使用 `formatTaskTimestamp`，避免截止时间与实际完成时间混淆。

- [ ] **Step 5: 创建 TaskGroup 并改造 TaskList**

```typescript
export function TaskGroupView(props: TaskGroupViewProps) {
  const serial = props.group.task.serial

  return (
    <article
      className="task-group"
      data-context-only={props.group.context_only || undefined}
    >
      <TaskRow task={props.group.task} {...props.rowProps} />
      {props.group.children.length > 0 ? (
        <div className="child-task-section">
          <p>
            子待办 {props.group.completed_child_count}/{props.group.child_count} 已完成
          </p>
          <ul aria-label={`#${serial} 的子待办`}>
            {props.group.children.map((child) => (
              <li key={child.id}>
                <TaskRow task={child} {...props.rowProps} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  )
}
```

`TaskList` props 从 `tasks: Task[]` 改为 `groups: TaskGroup[]`，根节点使用 `key={group.task.id}`。空状态根据 `TaskStatusFilter` 显示四种文本。

- [ ] **Step 6: 运行 workspace 测试**

Run:

```powershell
mise exec -- pnpm test:web -- todo-workspace.test.tsx
```

Expected: 快速创建、树展示、状态选择和失败回滚测试通过。

- [ ] **Step 7: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/quick-create-form.tsx apps/web/src/features/tasks/task-list.tsx apps/web/src/features/tasks/task-row.tsx apps/web/src/features/tasks/task-group.tsx apps/web/src/features/tasks/task-time.ts apps/web/src/features/tasks/task-time.test.ts apps/web/src/features/tasks/todo-workspace.test.tsx
git commit -m "feat(web): 展示三状态父子待办树"
```

## Task 5: 扩展编辑面板和删除提示

**Files:**
- Modify: `apps/web/src/features/tasks/task-editor-panel.tsx`
- Modify: `apps/web/src/features/tasks/task-editor-panel.test.tsx`
- Modify: `apps/web/src/features/tasks/delete-task-dialog.tsx`

- [ ] **Step 1: 改写编辑 fixture 和最小 PATCH 测试**

fixture 使用完整新版 Task，并断言：

```typescript
await user.clear(screen.getByLabelText("描述"))
await user.click(screen.getByRole("button", { name: "保存" }))
expect(screen.getByText("描述不能为空")).toBeInTheDocument()
expect(props.onSave).not.toHaveBeenCalled()
```

真实修改测试：

```typescript
await user.clear(screen.getByLabelText("描述"))
await user.type(screen.getByLabelText("描述"), "新的详细说明")
await user.clear(screen.getByLabelText("主题"))
await user.type(screen.getByLabelText("主题"), "工作")
await user.selectOptions(screen.getByLabelText("优先级"), "")
await user.selectOptions(screen.getByLabelText("状态"), "in_progress")
await user.click(screen.getByRole("button", { name: "保存" }))
expect(props.onSave).toHaveBeenCalledWith({
  description: "新的详细说明",
  topic: "工作",
  priority: null,
  status: "in_progress",
})
```

删除提示测试传 `childCount=2` 并断言“2 个子待办将成为顶层待办”。

- [ ] **Step 2: 运行编辑测试确认失败**

Run:

```powershell
mise exec -- pnpm test:web -- task-editor-panel.test.tsx
```

Expected: FAIL，旧编辑器仍使用备注和 `none` 优先级。

- [ ] **Step 3: 替换 EditorValues 和 patch 生成逻辑**

```typescript
type EditorValues = {
  title: string
  description: string
  topic: string
  priority: TaskPriority | ""
  status: TaskStatus
  dueAt: string
  parentId: string
}
```

`initialValues` 使用 `task.description`、`task.topic`、`task.priority ?? ""`、`task.status` 和 `task.parent_id ?? ""`。patch 对照规范化值：

```typescript
const description = values.description.trim()
const topic = values.topic.trim()
const priority = values.priority === "" ? null : values.priority
const parentId = values.parentId === "" ? null : values.parentId

if (description !== task.description) patch.description = description
if (topic !== task.topic) patch.topic = topic
if (priority !== task.priority) patch.priority = priority
if (values.status !== task.status) patch.status = values.status
if (parentId !== task.parent_id) patch.parent_id = parentId
```

提交前分别验证标题、描述、主题非空；错误文本使用中文并靠近字段展示。保留 DST 转换和 dirty 关闭确认。

- [ ] **Step 4: 更新编辑字段和只读元数据**

表单按以下顺序：

```text
#serial（只读）
标题
描述
主题
状态
优先级
截止时间
父待办
创建时间（只读）
完成时间（只读，仅 completed 时有值）
```

优先级空 option：

```tsx
<option value="">无优先级</option>
```

不要提供 completed_at 输入控件。

- [ ] **Step 5: 更新删除确认**

`DeleteTaskDialogProps` 增加 `childCount: number`，描述为：

```tsx
<Dialog.Description>
  “{taskTitle}”将被永久删除，此操作无法撤销。
  {childCount > 0
    ? ` ${childCount} 个子待办不会被删除，将成为顶层待办。`
    : ""}
</Dialog.Description>
```

- [ ] **Step 6: 运行编辑测试**

Run:

```powershell
mise exec -- pnpm test:web -- task-editor-panel.test.tsx
```

Expected: PASS。

- [ ] **Step 7: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/task-editor-panel.tsx apps/web/src/features/tasks/task-editor-panel.test.tsx apps/web/src/features/tasks/delete-task-dialog.tsx
git commit -m "feat(web): 扩展待办详情字段与删除提示"
```

## Task 6: 实现父待办选择和子待办创建

**Files:**
- Create: `apps/web/src/features/tasks/parent-task-picker.tsx`
- Create: `apps/web/src/features/tasks/child-task-create-form.tsx`
- Modify: `apps/web/src/features/tasks/task-editor-panel.tsx`
- Modify: `apps/web/src/features/tasks/task-editor-panel.test.tsx`
- Modify: `apps/web/src/features/tasks/todo-workspace.tsx`

- [ ] **Step 1: 写父级搜索和子待办创建失败测试**

mock `listParentOptions` 并覆盖：

- 打开父级选择器后请求第一页。
- 输入 `#7` 时取消旧请求并查询 `{ query: "#7", limit: 20 }`。
- 当前任务自身不显示在候选中。
- 选择父任务后只修改表单草稿，点击保存才提交 `parent_id`。
- “解除父待办”提交 `parent_id: null`。
- 根任务编辑器显示“添加子待办”；子任务编辑器不显示该入口。
- 子待办创建请求包含父 ID 和默认继承主题。

- [ ] **Step 2: 运行编辑测试确认组件不存在**

Run:

```powershell
mise exec -- pnpm test:web -- task-editor-panel.test.tsx
```

Expected: FAIL，找不到父级选择器和添加子待办表单。

- [ ] **Step 3: 创建 ParentTaskPicker**

props：

```typescript
type ParentTaskPickerProps = {
  currentTaskId: string
  value: ParentTaskOption | null
  disabled: boolean
  onChange(parent: ParentTaskOption | null): void
}
```

组件用独立 Dialog 和 AbortController 读取候选。输入变化后不增加 debounce 依赖；使用 250ms `setTimeout` 并在 effect cleanup 中 `clearTimeout`、abort 上一请求。分页追加时按 candidate ID 去重。

候选按钮可访问名称：

```tsx
aria-label={`选择 #${option.serial} ${option.title} 作为父待办`}
```

过滤 `option.id !== currentTaskId`。错误靠近候选列表显示并提供重试。

- [ ] **Step 4: 创建 ChildTaskCreateForm**

```typescript
type ChildTaskCreateFormProps = {
  parent: Task
  creating: boolean
  onCreate(input: TaskCreateInput): Promise<void>
}
```

表单显示标题和主题，主题初始为 `parent.topic`；提交：

```typescript
await onCreate({
  title: normalizedTitle,
  topic: normalizedTopic,
  parent_id: parent.id,
})
```

成功后清空标题、保留主题；失败保留两个字段并显示安全错误。

- [ ] **Step 5: 将父级和子创建接入编辑器**

`TaskEditorPanelProps` 增加：

```typescript
childCount: number
creatingChild: boolean
onCreateChild(input: TaskCreateInput): Promise<void>
```

当 `task.parent_id === null` 时显示 `ChildTaskCreateForm`；所有任务都显示 `ParentTaskPicker`，但已有子任务的根任务禁用父级选择并说明“一层父子关系下，拥有子待办的任务不能再成为子待办”。

`TodoWorkspace` 使用 `findTaskInGroups` 找选中任务，并从其分组计算 childCount；React key 保持 `key={selectedTask.id}`，切换任务时重置编辑快照。

- [ ] **Step 6: 运行编辑和 workspace 测试**

Run:

```powershell
mise exec -- pnpm test:web -- task-editor-panel.test.tsx todo-workspace.test.tsx
```

Expected: PASS。

- [ ] **Step 7: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/parent-task-picker.tsx apps/web/src/features/tasks/child-task-create-form.tsx apps/web/src/features/tasks/task-editor-panel.tsx apps/web/src/features/tasks/task-editor-panel.test.tsx apps/web/src/features/tasks/todo-workspace.tsx
git commit -m "feat(web): 支持父待办选择与子待办创建"
```

## Task 7: 组合两栏布局并完成响应式样式

**Files:**
- Modify: `apps/web/src/features/tasks/todo-workspace.tsx`
- Delete: `apps/web/src/features/tasks/task-toolbar.tsx`
- Modify: `apps/web/src/index.css`
- Modify: `apps/web/src/features/tasks/todo-workspace.test.tsx`

- [ ] **Step 1: 写最终语义结构和筛选摘要测试**

断言：

- header 在筛选和主列表之外且仍显示账号与退出。
- desktop aside 使用 `aria-label="任务筛选"`。
- 主区使用 `aria-label="Todo List"`。
- 当前筛选摘要显示 `In Progress · Tickly`，可以单独清除主题。
- `context_only` 父任务带“仅用于展示匹配的子待办”辅助文本。
- 编辑 Dialog 打开时 aside 和列表仍存在于 DOM，但焦点锁定在 Dialog。

- [ ] **Step 2: 组合 TodoWorkspaceLayout**

`TodoWorkspace` 主结构固定为：

```tsx
<main className="todo-page">
  <section className="todo-shell" aria-labelledby="workspace-title">
    <WorkspaceHeader />
    <div className="todo-workspace-layout">
      <TaskFilterSidebar {...filterProps} />
      <section className="task-list-content" aria-label="Todo List">
        <div className="task-list-heading">
          <div>
            <p className="auth-card-index">Todo list</p>
            <h1 id="workspace-title">今天要完成什么？</h1>
          </div>
          <MobileTaskFilterDialog {...mobileFilterProps} />
        </div>
        <QuickCreateForm {...quickCreateProps} />
        <ActiveFilterSummary {...summaryProps} />
        <TaskList {...listProps} />
      </section>
    </div>
  </section>
  {selectedTask !== null ? <TaskEditorPanel key={selectedTask.id} /> : null}
</main>
```

如果不单独创建 `WorkspaceHeader` 或 `ActiveFilterSummary` 文件，就把它们保留为 `todo-workspace.tsx` 内部小函数；不要为了两个局部消费者增加新目录。

- [ ] **Step 3: 添加两栏和筛选 CSS**

在 `apps/web/src/index.css` 替换原 hero/toolbar 主布局样式：

```css
.todo-workspace-layout {
  @apply grid min-h-[calc(100svh-8rem)] lg:grid-cols-[16rem_minmax(0,1fr)];
}

.task-filter-sidebar {
  @apply hidden border-r border-border/75 bg-muted/25 lg:block;
}

.task-filter-sidebar-inner {
  @apply sticky top-7 grid gap-7 p-6;
}

.task-list-content {
  @apply min-w-0 px-5 py-8 sm:px-8;
}

.task-list-heading {
  @apply flex items-start justify-between gap-4;
}

.mobile-task-filter-trigger {
  @apply lg:hidden;
}

.task-filter-section {
  @apply grid gap-3;
}

.task-filter-options {
  @apply grid gap-1;
}

.task-filter-options button {
  @apply min-h-11 rounded-xl px-3 text-left text-sm text-muted-foreground;
}

.task-filter-options button[aria-pressed="true"] {
  @apply bg-background font-medium text-foreground shadow-sm;
}
```

`task-list-panel` 移除旧的 `mx-5 sm:mx-8`，因为主区自己提供 padding。

- [ ] **Step 4: 添加任务树 CSS**

```css
.task-group + .task-group {
  @apply border-t border-border/80;
}

.task-group[data-context-only] > .task-row {
  @apply bg-muted/25;
}

.child-task-section {
  @apply ml-6 border-l border-blue-200 bg-blue-50/25 pl-4 sm:ml-10;
}

.child-task-section > p {
  @apply px-4 py-2 text-xs font-medium text-muted-foreground;
}

.child-task-section .task-row {
  @apply py-3;
}

.task-row-status {
  @apply min-h-9 rounded-lg border border-input bg-background px-2 text-xs;
}

.task-row[data-status="completed"] .task-row-title {
  @apply text-muted-foreground line-through decoration-slate-400;
}
```

移动端保持单列，不生成水平滚动；触控控件最小高度 44px。

- [ ] **Step 5: 删除旧 TaskToolbar 并清理引用**

删除 `task-toolbar.tsx`，清除 `.task-toolbar`、`.task-status-tabs` 和旧 checkbox CSS。运行：

```powershell
rg -n "TaskToolbar|task-toolbar|task-status-tabs|task-checkbox|is_completed" apps/web/src
```

Expected: 无生产代码命中；测试也不应再依赖旧复选框语义。

- [ ] **Step 6: 运行 workspace 测试**

Run:

```powershell
mise exec -- pnpm test:web -- todo-workspace.test.tsx
```

Expected: PASS。

- [ ] **Step 7: 条件式提交检查点**

```powershell
git add -- apps/web/src/features/tasks/todo-workspace.tsx apps/web/src/features/tasks/task-toolbar.tsx apps/web/src/index.css apps/web/src/features/tasks/todo-workspace.test.tsx
git commit -m "feat(web): 调整待办两栏响应式布局"
```

## Task 8: 校准文档中的当前事实

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/roadmaps/2026-07-26-tickly-zero-to-one.md`

- [ ] **Step 1: 更新 README 当前能力和 API 路径**

把当前能力描述改为包含：

```text
账号内流水编号、New/In Progress/Completed 三状态、必填自由文本主题、
可选截止时间、一层父子待办、桌面两栏筛选和移动筛选抽屉。
```

API 路径列表增加：

```text
/api/v1/tasks/topics
/api/v1/tasks/parent-options
```

仍明确 AI 尚未实现。

- [ ] **Step 2: 更新 AGENTS 当前状态**

只调整“当前状态”两条，保留所有开发、验证、认证和 AI 边界约束。Web 描述新版布局与树，API 描述新版任务模型。

- [ ] **Step 3: 校准路线图历史事实**

更新以下位置：

- 阶段 3 的状态筛选从 All/Active/Completed 改为 All/New/In Progress/Completed。
- `tasks` schema 表改为 `serial`、`description`、可空 priority、topic、status、parent_id。
- API query 和静态路径增加主题与父级候选。
- 从“不包含子任务”的旧范围中移除已经实现的一层子任务，但不要把多层树或项目管理描述成已实现。
- 当前状态段增加两栏布局，不修改阶段 5 AI 尚未实现的事实。
- 阶段 5 的未来任务草稿契约必须包含必填 `topic`，但本次只校准路线图文字，不实现 AI 请求、页面或模型调用。

- [ ] **Step 4: 文档事实检查**

Run:

```powershell
rg -n "active|is_completed|notes|priority.*none|不包含.*子任务" README.md AGENTS.md docs/roadmaps/2026-07-26-tickly-zero-to-one.md
```

Expected: 只允许明确说明历史迁移或非任务领域的命中；当前 Todo 契约不再描述旧字段。

- [ ] **Step 5: 条件式提交检查点**

```powershell
git add -- README.md AGENTS.md docs/roadmaps/2026-07-26-tickly-zero-to-one.md
git commit -m "docs: 更新待办模型与页面布局说明"
```

## Task 9: 完整验证与交付检查

**Files:**
- Verify only

- [ ] **Step 1: 运行 Web 行为测试**

Run:

```powershell
mise exec -- pnpm test:web
```

Expected: PASS，Vitest 无失败。

- [ ] **Step 2: 运行 Web 必需检查**

Run:

```powershell
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
```

Expected: 三条命令全部 exit 0。

- [ ] **Step 3: 回归 API 契约**

Run:

```powershell
mise exec -- pnpm test:api
```

Expected: PASS，确保 Web 使用的最终契约仍受后端测试保护。

- [ ] **Step 4: 检查禁止持久化和旧字段**

Run:

```powershell
rg -n "localStorage|sessionStorage|IndexedDB|is_completed|notes|TaskStatus.*active" apps/web/src/features/tasks apps/web/src/features/auth -g "!*.test.ts" -g "!*.test.tsx"
```

Expected: 无命中。

- [ ] **Step 5: 检查 diff 和工作区范围**

Run:

```powershell
git diff --check
git status --short
```

Expected: `git diff --check` 无输出；状态只包含两份计划、设计文档、API/Web/文档目标改动和任务开始前已有的 SQLite 临时文件。

- [ ] **Step 6: 人工响应式验收清单**

由验收者在桌面和手机宽度确认：

- 桌面左筛选栏与右列表同时可见，编辑 Dialog 不形成永久第三栏。
- 手机只显示单列列表，筛选通过 Dialog 打开。
- 长标题、长主题、多个子任务不会产生水平滚动。
- New、In Progress、Completed 不只依靠颜色区分。
- 截止时间和完成时间标签不会混淆。
- 键盘可以操作筛选、状态、加载更多、编辑、父级选择和 Dialog 关闭。
