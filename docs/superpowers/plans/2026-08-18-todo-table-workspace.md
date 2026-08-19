# Todo Table Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Todo 主区域重构为弱表头的响应式六列表格，并用响应式新建抽屉替换顶部快捷新增区域。

**Architecture:** 保留 `useTaskWorkspace` 的数据、互斥和刷新逻辑，展示层改为 `TaskList → TaskGroupView → TaskRow` 的语义表格链。新建行为由独立 `TaskCreatePanel` 管理表单快照、校验、时区转换和关闭保护，`TodoWorkspace` 只协调打开状态与现有 `actions.create`。

**Tech Stack:** React 19、TypeScript、Tailwind CSS 4、Base UI Dialog、Vitest 4、Testing Library

---

## 文件结构

- Create `apps/web/src/features/tasks/task-create-panel.tsx`：响应式新建抽屉及创建表单状态。
- Create `apps/web/src/features/tasks/task-create-panel.test.tsx`：新建抽屉字段、提交、错误与关闭保护测试。
- Create `apps/web/src/features/tasks/task-table.test.tsx`：语义表格、父子行和状态切换测试。
- Modify `apps/web/src/features/tasks/todo-workspace.tsx`：移除快捷新增，接入工具栏与新建抽屉。
- Modify `apps/web/src/features/tasks/todo-workspace.test.tsx`：迁移工作区级新建流程，删除快捷表单专属用例。
- Modify `apps/web/src/features/tasks/task-list.tsx`：输出 caption、弱表头和六列表格。
- Modify `apps/web/src/features/tasks/task-group.tsx`：输出 `<tbody>` 任务组。
- Modify `apps/web/src/features/tasks/task-row.tsx`：输出六列 `<tr>`。
- Modify `apps/web/src/features/tasks/layout-density.test.ts`：将旧卡片行契约更新为表格和移动折叠契约。
- Modify `apps/web/src/index.css`：删除快捷新增样式，新增表格与响应式行布局。
- Delete `apps/web/src/features/tasks/quick-create-form.tsx`：所有创建入口迁移后删除。

### Task 1: 新建待办抽屉

**Files:**
- Create: `apps/web/src/features/tasks/task-create-panel.test.tsx`
- Create: `apps/web/src/features/tasks/task-create-panel.tsx`
- Reference: `apps/web/src/features/tasks/task-editor-panel.tsx`
- Reference: `apps/web/src/features/tasks/task-time.ts`

- [ ] **Step 1: 写入新建抽屉失败测试**

测试使用 `TaskCreatePanel` 的以下公开属性：

```ts
export type TaskCreatePanelProps = {
  selectedTopic?: string
  topicOptions: string[]
  timeZone: string
  creating: boolean
  onCreate(input: TaskCreateInput): Promise<void>
  onClose(): void
}
```

在 `task-create-panel.test.tsx` 中覆盖：

```tsx
it("预填当前主题并提交规范化后的完整创建参数", async () => {
  const user = userEvent.setup()
  const onCreate = vi.fn().mockResolvedValue(undefined)
  const onClose = vi.fn()
  render(
    <TaskCreatePanel
      selectedTopic="Tickly"
      topicOptions={["Tickly", "工作"]}
      timeZone="Asia/Shanghai"
      creating={false}
      onCreate={onCreate}
      onClose={onClose}
    />
  )

  await user.type(screen.getByLabelText("标题"), "  表格化列表  ")
  await user.type(screen.getByLabelText("描述（可选）"), "  调整主视图  ")
  await user.selectOptions(screen.getByLabelText("优先级"), "medium")
  await user.type(screen.getByLabelText("截止时间"), "2026-08-20T18:00")
  await user.click(screen.getByRole("button", { name: "创建待办" }))

  expect(onCreate).toHaveBeenCalledWith({
    title: "表格化列表",
    description: "调整主视图",
    topic: "Tickly",
    priority: "medium",
    due_at: "2026-08-20T10:00:00.000Z",
  })
  expect(onClose).toHaveBeenCalledOnce()
})
```

再添加独立用例验证：标题/主题空值显示中文字段错误且不提交；空描述、空优先级和空截止时间从 payload 省略；`creating` 时按钮显示“正在创建”并禁用；请求失败显示“任务创建失败”且保留输入；有真实修改时关闭需要 `window.confirm`，仅主题预填不算修改；Dialog 焦点保持在抽屉内。

- [ ] **Step 2: 运行测试并确认组件缺失导致失败**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/task-create-panel.test.tsx
```

Expected: FAIL，提示无法解析 `./task-create-panel`。

- [ ] **Step 3: 实现最小 `TaskCreatePanel`**

实现要求：

```tsx
const initialValues = {
  title: "",
  description: "",
  topic: selectedTopic ?? "",
  priority: "" as TaskPriority | "",
  dueAt: "",
}

const dirty =
  values.title.trim() !== "" ||
  values.description.trim() !== "" ||
  values.topic.trim() !== initialValues.topic ||
  values.priority !== "" ||
  values.dueAt !== ""
```

提交时构造最小 payload：

```ts
const input: TaskCreateInput = {
  title: values.title.trim(),
  topic: values.topic.trim(),
}
if (values.description.trim() !== "") {
  input.description = values.description.trim()
}
if (values.priority !== "") {
  input.priority = values.priority
}
if (values.dueAt !== "") {
  input.due_at = toUtcDueAt(values.dueAt, timeZone)
}
```

使用 `Dialog.Root open`、Backdrop、Viewport 和 Popup，沿用编辑面板的移动端底部全宽、桌面右侧抽屉类。`requestClose` 在 `creating` 时取消关闭；非创建中且 `dirty` 时先执行 `window.confirm("放弃未保存的修改？")`。捕获 `TaskTimeError` 到截止时间字段，其他错误用 `safeErrorMessage(error, "任务创建失败")`。

- [ ] **Step 4: 运行抽屉测试并确认通过**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/task-create-panel.test.tsx
```

Expected: PASS，所有新建抽屉用例通过。

### Task 2: 六列语义表格

**Files:**
- Create: `apps/web/src/features/tasks/task-table.test.tsx`
- Modify: `apps/web/src/features/tasks/task-list.tsx`
- Modify: `apps/web/src/features/tasks/task-group.tsx`
- Modify: `apps/web/src/features/tasks/task-row.tsx`

- [ ] **Step 1: 写入语义表格失败测试**

在 `task-table.test.tsx` 构造一个含根任务与子待办的 `TaskGroup`，并断言：

```tsx
expect(screen.getByRole("table", { name: "Todo List" })).toBeInTheDocument()
expect(
  screen.getAllByRole("columnheader").map((header) => header.textContent)
).toEqual(["#", "待办", "主题", "优先级", "截止时间", "状态"])
expect(screen.getByRole("button", { name: "编辑 根任务" })).toBeInTheDocument()
expect(screen.getByText("1/2 已完成")).toBeInTheDocument()
expect(screen.getByRole("row", { name: /子任务/ })).toHaveAttribute(
  "data-child",
  "true"
)
```

补充用例：无优先级和无截止时间显示 `—`；`context_only` 提示位于根任务行；状态下拉调用 `onStatusChange(task, nextStatus)`；空状态、加载、首屏错误、增量错误和加载更多仍提供现有可访问文本。

- [ ] **Step 2: 运行表格测试并确认旧列表结构失败**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/task-table.test.tsx
```

Expected: FAIL，找不到 `table` 和六个 `columnheader`。

- [ ] **Step 3: 将 `TaskRow` 改为六列表格行**

扩展属性：

```ts
type TaskRowProps = {
  task: Task
  timeZone: string
  statusMutating: boolean
  child?: boolean
  progress?: { completed: number; total: number }
  contextOnly?: boolean
  onSelect(task: Task): void
  onStatusChange(task: Task, status: TaskStatus): Promise<void>
}
```

返回结构固定为：

```tsx
<tr className="task-row" data-status={task.status} data-child={child || undefined}>
  <td className="task-row-serial">#{task.serial}</td>
  <td className="task-row-task">
    <button className="task-row-main" onClick={() => onSelect(task)}>
      <span className="task-row-title">{task.title}</span>
      {progress ? <span className="task-row-progress">{progress.completed}/{progress.total} 已完成</span> : null}
      {contextOnly ? <span className="task-context-note">仅用于展示匹配的子待办</span> : null}
    </button>
  </td>
  <td className="task-row-topic">{task.topic}</td>
  <td className="task-row-priority">{priorityLabel ?? "—"}</td>
  <td className="task-row-due">{task.due_at ? formatDueLabel(task.due_at, timeZone) : "—"}</td>
  <td className="task-row-status">{/* 现有状态 select */}</td>
</tr>
```

完成状态继续对标题应用删除线；状态 option 值和标签保持现有契约。

- [ ] **Step 4: 将任务组和列表改为合法表格结构**

`TaskGroupView` 返回一个 `<tbody className="task-group">`，根任务传入 `progress`/`contextOnly`，子任务传入 `child`。`TaskList` 在有数据时输出：

```tsx
<table className="task-table">
  <caption className="sr-only">Todo List</caption>
  <colgroup>{/* 六列宽度 class */}</colgroup>
  <thead className="task-table-head">
    <tr>
      <th scope="col">#</th>
      <th scope="col">待办</th>
      <th scope="col">主题</th>
      <th scope="col">优先级</th>
      <th scope="col">截止时间</th>
      <th scope="col">状态</th>
    </tr>
  </thead>
  {groups.map((group) => <TaskGroupView key={group.task.id} {...props} />)}
</table>
```

空状态不伪造表格行；继续使用现有 `task-empty-state`。增量错误和加载更多保留在表格之后。

- [ ] **Step 5: 运行表格测试并确认通过**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/task-table.test.tsx
```

Expected: PASS，表格、父子行、状态切换和状态卡用例全部通过。

### Task 3: 工作区接入新建抽屉

**Files:**
- Modify: `apps/web/src/features/tasks/todo-workspace.test.tsx`
- Modify: `apps/web/src/features/tasks/todo-workspace.tsx`
- Delete: `apps/web/src/features/tasks/quick-create-form.tsx`

- [ ] **Step 1: 将工作区创建测试改为按钮与抽屉流程**

把原“任务标题/任务主题直接可见”的创建用例改为：

```tsx
expect(screen.queryByLabelText("任务标题")).not.toBeInTheDocument()
await user.click(await screen.findByRole("button", { name: "新建待办" }))
expect(screen.getByRole("dialog", { name: "新建待办" })).toBeInTheDocument()
expect(screen.getByLabelText("主题")).toHaveValue("Tickly")
await user.type(screen.getByLabelText("标题"), "阶段 5")
await user.click(screen.getByRole("button", { name: "创建待办" }))
expect(tasks.createTask).toHaveBeenCalledWith({
  title: "阶段 5",
  topic: "Tickly",
})
```

删除 `QuickCreateForm` import 及“selectedTopic 仅同步空值或上一选择”专属 describe；主题预填和覆盖行为已转移到 `TaskCreatePanel` 测试。保留创建成功刷新、失败错误域和结构变更互斥相关工作区断言。

- [ ] **Step 2: 运行工作区测试并确认旧 UI 导致失败**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/todo-workspace.test.tsx
```

Expected: FAIL，找不到“新建待办”按钮或对话框。

- [ ] **Step 3: 接入紧凑工具栏和新建抽屉**

在 `TodoWorkspace` 增加：

```tsx
const [createOpen, setCreateOpen] = useState(false)
```

用以下工具栏替换大标题和 `QuickCreateForm`：

```tsx
<div className="task-list-toolbar">
  <p className="auth-card-index">Todo list</p>
  <Button type="button" onClick={() => setCreateOpen(true)}>
    <Plus aria-hidden="true" />
    新建待办
  </Button>
</div>
```

在工作区末尾条件渲染：

```tsx
{createOpen ? (
  <TaskCreatePanel
    selectedTopic={state.query.topic}
    topicOptions={state.topics}
    timeZone={timeZone}
    creating={state.creating}
    onCreate={actions.create}
    onClose={() => setCreateOpen(false)}
  />
) : null}
```

删除 `QuickCreateForm` 引用和文件。保留编辑抽屉的独立状态与行为。

- [ ] **Step 4: 运行工作区与新建抽屉测试**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/todo-workspace.test.tsx src/features/tasks/task-create-panel.test.tsx
```

Expected: PASS，工作区集成和抽屉组件测试全部通过。

### Task 4: 表格视觉与响应式契约

**Files:**
- Modify: `apps/web/src/features/tasks/layout-density.test.ts`
- Modify: `apps/web/src/index.css`

- [ ] **Step 1: 将排版契约改为表格规则并确认失败**

保留 `1440px` 主体、`272px` 筛选栏和 `min(40rem,50vw)` 编辑抽屉断言；替换旧 `.task-row` 卡片内边距断言：

```ts
expect(todoStyles).toMatch(/\.task-table\s*\{[^}]*table-layout: fixed/s)
expect(todoStyles).toMatch(/\.task-table-head th\s*\{[^}]*text-muted-foreground/s)
expect(todoStyles).toMatch(/@media \(max-width: 63\.999rem\)[\s\S]*\.task-row\s*\{[^}]*grid-template-areas/s)
expect(todoStyles).not.toContain(".quick-create {")
```

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/layout-density.test.ts
```

Expected: FAIL，当前 CSS 尚无表格和移动端 grid areas 规则。

- [ ] **Step 2: 删除快捷新增样式并实现桌面弱表头**

删除 `.quick-create`、`.quick-create-control` 及其 input/button 规则。新增：

```css
.task-list-toolbar {
  @apply flex items-center justify-between gap-4;
}

.task-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}

.task-table-head th {
  @apply border-b border-border/70 px-3 py-2 text-left font-mono text-[0.66rem] font-medium tracking-[0.08em] text-muted-foreground uppercase;
}

.task-row > td {
  @apply border-t border-border/65 px-3 py-3 align-middle;
}
```

为六个 `<col>`、任务按钮、主题、优先级、截止时间和状态单元格添加稳定宽度、截断、颜色和焦点样式。`tbody + tbody` 的首行使用稍强分隔线；子行待办单元格增加缩进和蓝色细引导线。不要恢复单元格竖边框或醒目表头背景。

- [ ] **Step 3: 实现同 DOM 的移动端折叠布局**

在 `@media (max-width: 63.999rem)` 中视觉隐藏表头但保留辅助技术可读性，并把每个 `.task-row` 改为：

```css
.task-row {
  display: grid;
  grid-template-columns: 3rem minmax(0, 1fr) auto;
  grid-template-areas:
    "serial task status"
    ". topic status"
    ". priority due";
  gap: 0.25rem 0.75rem;
  padding: 0.75rem 1rem;
}
```

六个单元格分别映射 grid area，并在移动端去掉 table-cell padding/border。新建与编辑 Popup 继续使用现有移动全宽、桌面右侧抽屉类。

- [ ] **Step 4: 运行排版契约和完整任务组件测试**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/layout-density.test.ts src/features/tasks/task-table.test.tsx src/features/tasks/task-create-panel.test.tsx src/features/tasks/todo-workspace.test.tsx
```

Expected: PASS，排版与任务 UI 相关用例全部通过。

### Task 5: 完整验证与范围复核

**Files:**
- Verify: `apps/web/src/features/tasks/task-create-panel.tsx`
- Verify: `apps/web/src/features/tasks/task-create-panel.test.tsx`
- Verify: `apps/web/src/features/tasks/task-table.test.tsx`
- Verify: `apps/web/src/features/tasks/todo-workspace.tsx`
- Verify: `apps/web/src/features/tasks/todo-workspace.test.tsx`
- Verify: `apps/web/src/features/tasks/task-list.tsx`
- Verify: `apps/web/src/features/tasks/task-group.tsx`
- Verify: `apps/web/src/features/tasks/task-row.tsx`
- Verify: `apps/web/src/features/tasks/layout-density.test.ts`
- Verify: `apps/web/src/index.css`

- [ ] **Step 1: 格式化本次 TypeScript/TSX 文件**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec prettier --write src/features/tasks/task-create-panel.tsx src/features/tasks/task-create-panel.test.tsx src/features/tasks/task-table.test.tsx src/features/tasks/todo-workspace.tsx src/features/tasks/todo-workspace.test.tsx src/features/tasks/task-list.tsx src/features/tasks/task-group.tsx src/features/tasks/task-row.tsx src/features/tasks/layout-density.test.ts
```

Expected: exit code 0，仅格式化列出的任务 UI 文件。

- [ ] **Step 2: 运行 Web lint**

Run: `mise exec -- pnpm lint`

Expected: exit code 0，无 ESLint 错误。

- [ ] **Step 3: 运行 Web typecheck**

Run: `mise exec -- pnpm typecheck`

Expected: exit code 0，无 TypeScript 错误。

- [ ] **Step 4: 运行完整 Web 测试**

Run: `mise exec -- pnpm test:web`

Expected: exit code 0，所有 Vitest 文件和用例通过。

- [ ] **Step 5: 运行生产构建**

Run: `mise exec -- pnpm build`

Expected: exit code 0，TypeScript build 与 Vite build 完成；允许现有 `__dirname` native config 提示，但不得出现新 warning/error。

- [ ] **Step 6: 检查差异与编译 CSS**

Run:

```bash
git diff --check -- apps/web/src
git diff -- apps/web/src/features/tasks apps/web/src/index.css
git status --short
```

Expected: 无 whitespace error；业务差异仅覆盖表格展示、新建抽屉、快捷新增删除和对应测试。确认 `apps/web/dist` 未进入 Git 状态，新建抽屉和编辑抽屉的新宽度 utility 存在，旧快捷新增样式不进入产物。

> Git 提交不属于自动执行步骤；仅在用户明确说“提交”后按精确路径暂存并提交。当前工作区的 remote MCP 文档属于并行改动，不纳入本计划。
