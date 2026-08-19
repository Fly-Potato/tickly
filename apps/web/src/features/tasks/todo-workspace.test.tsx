import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  DEFAULT_TASK_QUERY,
  type SortOrder,
  type Task,
  type TaskGroup,
  type TaskListQuery,
  type TaskSort,
  type TaskStatusFilter,
} from "./task-api"
import type { WorkspaceQuery } from "./use-task-workspace"

const tasks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  listTaskTopics: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  deleteTask: vi.fn(),
}))

vi.mock("./task-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./task-api")>()),
  ...tasks,
}))

import { TodoWorkspace } from "./todo-workspace"
import { MobileTaskFilterDialog } from "./mobile-task-filter-dialog"
import { TaskFilterSidebar } from "./task-filter-sidebar"
import { TaskList } from "./task-list"

type FilterTestHarnessProps = {
  query?: WorkspaceQuery
  disabled?: boolean
  topicLoading?: boolean
  topicError?: string | null
  onStatusChange?(status: TaskStatusFilter): void
  onTopicChange?(topic: string | undefined): void
  onSortChange?(sort: TaskSort): void
  onOrderChange?(order: SortOrder): void
  onRetryTopics?(): Promise<void> | void
  onReset?(): void
  onApply?(query: WorkspaceQuery): void
}

const defaultFilterQuery: WorkspaceQuery = { ...DEFAULT_TASK_QUERY }

function FilterTestHarness({
  query = defaultFilterQuery,
  disabled = false,
  topicLoading = false,
  topicError = null,
  onStatusChange = vi.fn(),
  onTopicChange = vi.fn(),
  onSortChange = vi.fn(),
  onOrderChange = vi.fn(),
  onRetryTopics = vi.fn(),
  onReset = vi.fn(),
  onApply = vi.fn(),
}: FilterTestHarnessProps) {
  const sharedProps = {
    query,
    topics: ["Tickly", "工作"],
    disabled,
  }

  return (
    <>
      <TaskFilterSidebar
        {...sharedProps}
        topicLoading={topicLoading}
        topicError={topicError}
        onStatusChange={onStatusChange}
        onTopicChange={onTopicChange}
        onSortChange={onSortChange}
        onOrderChange={onOrderChange}
        onRetryTopics={onRetryTopics}
        onReset={onReset}
      />
      <main aria-label="Todo List" />
      <MobileTaskFilterDialog
        {...sharedProps}
        topicLoading={topicLoading}
        topicError={topicError}
        onRetryTopics={onRetryTopics}
        onApply={onApply}
      />
    </>
  )
}

function makeTask(id: string, overrides: Partial<Task> = {}): Task {
  return {
    id,
    serial: 1,
    title: `任务 ${id}`,
    description: "",
    priority: null,
    topic: "Tickly",
    status: "new",
    due_at: null,
    completed_at: null,
    parent_id: null,
    created_at: "2026-07-28T08:00:00Z",
    updated_at: "2026-07-28T08:00:00Z",
    ...overrides,
  }
}

function makeGroup(
  task: Task,
  children: Task[] = [],
  overrides: Partial<TaskGroup> = {}
): TaskGroup {
  return {
    task,
    children,
    child_count: children.length,
    completed_child_count: children.filter(
      (child) => child.status === "completed"
    ).length,
    context_only: false,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function renderWorkspace(onLogout = vi.fn().mockResolvedValue(undefined)) {
  render(
    <TodoWorkspace
      username="potato"
      timeZone="Asia/Shanghai"
      loggingOut={false}
      onLogout={onLogout}
    />
  )
  return { onLogout }
}

beforeEach(() => {
  Object.values(tasks).forEach((mock) => mock.mockReset())
  tasks.listTaskTopics.mockResolvedValue(["Tickly", "工作"])
})

describe("Todo 工作区", () => {
  it("展示账号、父子任务语义、时间和退出入口，任务主体打开编辑面板", async () => {
    const root = makeTask("root", {
      serial: 18,
      title: "完成阶段 4",
      priority: "high",
      created_at: "2026-08-17T08:30:00Z",
    })
    const child = makeTask("child", {
      serial: 19,
      title: "补充回归测试",
      status: "completed",
      parent_id: root.id,
      completed_at: "2026-08-17T09:45:00Z",
    })
    tasks.listTasks.mockResolvedValue({
      items: [
        makeGroup(root, [child], {
          child_count: 2,
          completed_child_count: 1,
          context_only: true,
        }),
      ],
      next_cursor: null,
    })
    const user = userEvent.setup()
    const { onLogout } = renderWorkspace()

    expect(
      await screen.findByRole("heading", { name: "Todo list" })
    ).toBeInTheDocument()
    expect(screen.getByText("potato")).toBeInTheDocument()
    expect(screen.getByText("Asia/Shanghai")).toBeInTheDocument()
    expect(screen.getByText("高优先级")).toBeInTheDocument()
    expect(screen.getByText("#18")).toBeInTheDocument()
    expect(screen.getByText("1/2 已完成")).toBeInTheDocument()
    expect(screen.getByRole("row", { name: /补充回归测试/ })).toHaveAttribute(
      "data-child",
      "true"
    )
    expect(
      screen.getByRole("combobox", { name: "设置 #18 的状态" })
    ).toHaveValue("new")
    expect(
      screen.getByRole("combobox", { name: "设置 #19 的状态" })
    ).toHaveValue("completed")
    expect(screen.queryByText("创建 · 8月17日 16:30")).not.toBeInTheDocument()
    expect(screen.queryByText("完成 · 8月17日 17:45")).not.toBeInTheDocument()
    expect(screen.getByText("补充回归测试")).toHaveClass("line-through")
    expect(screen.getByText("#18").closest(".task-group")).toHaveAttribute(
      "data-context-only",
      "true"
    )
    expect(screen.getByText("仅用于展示匹配的子待办")).toBeInTheDocument()

    const header = screen.getByRole("banner")
    const sidebar = screen.getByRole("complementary", { name: "任务筛选" })
    const listContent = screen.getByRole("region", { name: "Todo List" })
    expect(header).toContainElement(screen.getByText("potato"))
    expect(header).toContainElement(
      screen.getByRole("button", { name: "退出登录" })
    )
    expect(header).not.toContainElement(sidebar)
    expect(header).not.toContainElement(listContent)

    await user.click(screen.getByRole("button", { name: "退出登录" }))
    expect(onLogout).toHaveBeenCalledOnce()

    await user.click(screen.getByRole("button", { name: "编辑 完成阶段 4" }))
    expect(
      screen.getByRole("heading", { name: "编辑任务" })
    ).toBeInTheDocument()
    const editorDialog = screen.getByRole("dialog", { name: "编辑任务" })
    expect(document.body).toContainElement(sidebar)
    expect(document.body).toContainElement(listContent)
    expect(editorDialog).toContainElement(document.activeElement as HTMLElement)
    await user.tab({ shift: true })
    const activeElement = document.activeElement as HTMLElement
    expect(
      editorDialog.contains(activeElement) ||
        activeElement.hasAttribute("data-base-ui-focus-guard")
    ).toBe(true)
    await user.click(screen.getByRole("button", { name: "删除任务" }))
    expect(
      screen.getByText(/2 个子待办不会被删除，将成为顶层待办/)
    ).toBeInTheDocument()
  })

  it("通过新建抽屉校验必填项并提交规范化内容", async () => {
    const created = makeTask("created", {
      serial: 20,
      title: "阶段 4",
      topic: "Tickly",
    })
    const creation = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({
        items: [makeGroup(created)],
        next_cursor: null,
      })
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const user = userEvent.setup()
    renderWorkspace()
    expect(
      await screen.findByText("还没有任务，先写下第一件事。")
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("任务标题")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "新建待办" }))
    const dialog = screen.getByRole("dialog", { name: "新建待办" })
    const titleInput = within(dialog).getByLabelText("标题")
    const topicInput = within(dialog).getByLabelText("主题")
    const submit = within(dialog).getByRole("button", { name: "创建待办" })

    await user.type(titleInput, "  阶段 4  ")
    await user.click(submit)
    expect(within(dialog).getByText("主题不能为空")).toBeInTheDocument()
    expect(tasks.createTask).not.toHaveBeenCalled()

    await user.type(topicInput, "  Tickly  ")
    await user.click(submit)

    expect(tasks.createTask).toHaveBeenCalledWith({
      title: "阶段 4",
      topic: "Tickly",
    })
    expect(submit).toHaveTextContent("正在创建")
    expect(titleInput).toBeDisabled()
    await act(async () => {
      creation.resolve(created)
      await creation.promise
    })
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "新建待办" })
      ).not.toBeInTheDocument()
    )
    expect(await screen.findByText("阶段 4")).toBeInTheDocument()
  })

  it("选择子任务删除时不会沿用父任务的子待办数量", async () => {
    const root = makeTask("parent", { serial: 31, title: "父任务" })
    const child = makeTask("child", {
      serial: 32,
      title: "子任务",
      parent_id: root.id,
    })
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root, [child], { child_count: 2 })],
      next_cursor: null,
    })
    const user = userEvent.setup()
    renderWorkspace()

    await user.click(await screen.findByRole("button", { name: "编辑 子任务" }))
    expect(screen.getByText("#31 父任务")).toBeInTheDocument()
    expect(screen.queryByLabelText("子待办标题")).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "删除任务" }))

    expect(screen.getByText(/“子任务”将被永久删除/)).toBeInTheDocument()
    expect(screen.queryByText(/个子待办不会被删除/)).not.toBeInTheDocument()
  })

  it("根任务编辑器把父 ID 和继承主题交给现有创建动作", async () => {
    const root = makeTask("parent", {
      serial: 41,
      title: "父任务",
      topic: "工作",
    })
    const child = makeTask("child", {
      serial: 42,
      title: "新子任务",
      topic: "工作",
      parent_id: root.id,
    })
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root, [child])],
      next_cursor: null,
    })
    tasks.createTask.mockResolvedValue(child)
    const user = userEvent.setup()
    renderWorkspace()

    await user.click(await screen.findByRole("button", { name: "编辑 父任务" }))
    expect(screen.getByLabelText("子待办主题")).toHaveValue("工作")
    await user.type(screen.getByLabelText("子待办标题"), "新子任务{Enter}")

    expect(tasks.createTask).toHaveBeenCalledWith({
      title: "新子任务",
      topic: "工作",
      parent_id: root.id,
    })
  })

  it("分页父任务创建子待办后刷新第一页仍保留编辑上下文和分组计数", async () => {
    const firstRoot = makeTask("first", { serial: 40, title: "第一页任务" })
    const pagedParent = makeTask("paged-parent", {
      serial: 41,
      title: "第二页父任务",
      topic: "工作",
    })
    const createdChild = makeTask("created-child", {
      serial: 42,
      title: "分页子任务",
      topic: "工作",
      parent_id: pagedParent.id,
    })
    const firstPage = {
      items: [makeGroup(firstRoot)],
      next_cursor: "page-2",
    }
    tasks.listTasks
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce({
        items: [makeGroup(pagedParent, [], { child_count: 2 })],
        next_cursor: null,
      })
      .mockResolvedValueOnce(firstPage)
    tasks.createTask.mockResolvedValueOnce(createdChild)
    const user = userEvent.setup()
    renderWorkspace()

    await user.click(await screen.findByRole("button", { name: "加载更多" }))
    await user.click(
      await screen.findByRole("button", { name: "编辑 第二页父任务" })
    )
    const title = screen.getByLabelText("子待办标题")
    const topic = screen.getByLabelText("子待办主题")
    await user.type(title, "分页子任务{Enter}")

    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(3))
    expect(
      screen.getByRole("heading", { name: "编辑任务" })
    ).toBeInTheDocument()
    expect(title).toHaveValue("")
    expect(topic).toHaveValue("工作")
    expect(screen.getByText("0/3 已完成")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "编辑 分页子任务", hidden: true })
    ).toBeInTheDocument()
  })

  it("桌面筛选即时更新列表并允许只清除当前主题", async () => {
    tasks.listTasks.mockResolvedValue({ items: [], next_cursor: null })
    const user = userEvent.setup()
    renderWorkspace()

    await screen.findByText("还没有任务，先写下第一件事。")
    await user.click(screen.getByRole("button", { name: "In Progress" }))
    await waitFor(() =>
      expect(tasks.listTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "in_progress" }),
        expect.any(AbortSignal)
      )
    )
    await user.click(screen.getByRole("button", { name: "Tickly" }))
    await waitFor(() =>
      expect(tasks.listTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "in_progress", topic: "Tickly" }),
        expect.any(AbortSignal)
      )
    )

    const summary = screen.getByRole("region", { name: "当前筛选" })
    expect(summary).toHaveTextContent("In Progress · Tickly")
    expect(screen.getByRole("status", { name: "筛选变化" })).toHaveTextContent(
      "当前筛选：In Progress · Tickly"
    )
    await user.click(
      within(summary).getByRole("button", { name: "清除主题筛选 Tickly" })
    )
    await waitFor(() =>
      expect(tasks.listTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "in_progress" }),
        expect.any(AbortSignal)
      )
    )
    expect(tasks.listTasks.mock.lastCall?.[0]).toHaveProperty(
      "topic",
      undefined
    )
    expect(screen.getByRole("region", { name: "当前筛选" })).toHaveTextContent(
      "In Progress"
    )
    expect(screen.getByRole("status", { name: "筛选变化" })).toHaveTextContent(
      "当前筛选：In Progress"
    )

    expect(tasks.listTasks).not.toHaveBeenCalledWith(
      expect.objectContaining({ status: "active" }),
      expect.any(AbortSignal)
    )
  })

  it("筛选播报节点稳定挂载并与视觉摘要显隐同步", async () => {
    tasks.listTasks.mockResolvedValue({ items: [], next_cursor: null })
    const user = userEvent.setup()
    renderWorkspace()

    await screen.findByText("还没有任务，先写下第一件事。")
    const liveStatus = screen.getByRole("status", { name: "筛选变化" })
    expect(liveStatus).toHaveTextContent("当前筛选：无")
    expect(
      screen.queryByRole("region", { name: "当前筛选" })
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "In Progress" }))
    expect(
      await screen.findByRole("region", { name: "当前筛选" })
    ).toHaveTextContent("In Progress")
    expect(screen.getByRole("status", { name: "筛选变化" })).toBe(liveStatus)
    expect(liveStatus).toHaveTextContent("当前筛选：In Progress")

    await user.click(screen.getByRole("button", { name: "全部" }))
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "当前筛选" })
      ).not.toBeInTheDocument()
    )
    expect(screen.getByRole("status", { name: "筛选变化" })).toBe(liveStatus)
    expect(liveStatus).toHaveTextContent("当前筛选：无")
  })

  it("移动筛选在 Dialog 内独立重试主题且不应用筛选草稿", async () => {
    const topicRetry = deferred<string[]>()
    tasks.listTasks.mockResolvedValue({ items: [], next_cursor: null })
    tasks.listTaskTopics
      .mockRejectedValueOnce(new Error("provider secret"))
      .mockReturnValueOnce(topicRetry.promise)
    const user = userEvent.setup()
    renderWorkspace()

    await screen.findByText("还没有任务，先写下第一件事。")
    await waitFor(() => expect(tasks.listTaskTopics).toHaveBeenCalledOnce())
    const initialListCalls = tasks.listTasks.mock.calls.length

    await user.click(screen.getByRole("button", { name: "筛选" }))
    const dialog = screen.getByRole("dialog", { name: "筛选与排序" })
    expect(within(dialog).getByRole("alert")).toHaveTextContent("主题加载失败")

    await user.click(
      within(dialog).getByRole("button", { name: "In Progress" })
    )
    expect(tasks.listTasks).toHaveBeenCalledTimes(initialListCalls)

    await user.click(within(dialog).getByRole("button", { name: "重试主题" }))
    expect(tasks.listTaskTopics).toHaveBeenCalledTimes(2)
    expect(within(dialog).getByRole("status")).toHaveTextContent("正在加载主题")
    expect(
      within(dialog).getByRole("button", { name: "全部主题" })
    ).toBeDisabled()
    expect(dialog).toBeInTheDocument()
    expect(tasks.listTasks).toHaveBeenCalledTimes(initialListCalls)

    await act(async () => {
      topicRetry.resolve(["Tickly", "工作"])
      await topicRetry.promise
    })
    expect(
      await within(dialog).findByRole("button", { name: "Tickly" })
    ).toBeInTheDocument()
    expect(
      within(dialog).getByRole("button", { name: "In Progress" })
    ).toHaveAttribute("aria-pressed", "true")
    expect(dialog).toBeInTheDocument()
    expect(tasks.listTasks).toHaveBeenCalledTimes(initialListCalls)
  })

  it("创建失败保留标题和主题并显示安全错误", async () => {
    tasks.listTasks.mockResolvedValue({ items: [], next_cursor: null })
    tasks.createTask.mockRejectedValue(new Error("secret network detail"))
    const user = userEvent.setup()
    renderWorkspace()
    await screen.findByText("还没有任务，先写下第一件事。")
    await user.click(screen.getByRole("button", { name: "新建待办" }))
    const dialog = screen.getByRole("dialog", { name: "新建待办" })
    const titleInput = within(dialog).getByLabelText("标题")
    const topicInput = within(dialog).getByLabelText("主题")

    await user.type(titleInput, "保留标题")
    await user.type(topicInput, "工作")
    await user.click(within(dialog).getByRole("button", { name: "创建待办" }))

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "任务创建失败"
    )
    expect(titleInput).toHaveValue("保留标题")
    expect(topicInput).toHaveValue("工作")
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument()
  })

  it("使用当前 cursor 加载更多任务组", async () => {
    const first = makeGroup(makeTask("first", { serial: 1 }))
    const next = makeGroup(makeTask("next", { serial: 2 }), [], {
      child_count: 3,
    })
    tasks.listTasks
      .mockResolvedValueOnce({ items: [first], next_cursor: "initial-cursor" })
      .mockResolvedValueOnce({ items: [next], next_cursor: null })
    const user = userEvent.setup()
    renderWorkspace()
    await screen.findByText("任务 first")

    await user.click(await screen.findByRole("button", { name: "加载更多" }))

    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "initial-cursor" }),
      expect.any(AbortSignal)
    )
    expect(await screen.findByText("任务 next")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "编辑 任务 next" }))
    await user.click(screen.getByRole("button", { name: "删除任务" }))
    expect(
      screen.getByText(/3 个子待办不会被删除，将成为顶层待办/)
    ).toBeInTheDocument()
  })

  it("根任务状态先乐观更新并在服务端成功后重载任务树", async () => {
    const target = makeTask("target", {
      serial: 21,
      title: "推进任务",
    })
    const mutation = deferred<Task>()
    const updated = { ...target, status: "in_progress" as const }
    tasks.listTasks
      .mockResolvedValueOnce({
        items: [makeGroup(target)],
        next_cursor: null,
      })
      .mockResolvedValueOnce({
        items: [makeGroup(updated)],
        next_cursor: null,
      })
    tasks.updateTask.mockReturnValueOnce(mutation.promise)
    const user = userEvent.setup()
    renderWorkspace()
    const status = await screen.findByRole("combobox", {
      name: "设置 #21 的状态",
    })

    await user.selectOptions(status, "in_progress")
    expect(status).toHaveValue("in_progress")
    expect(status).toBeDisabled()
    expect(tasks.updateTask).toHaveBeenCalledWith("target", {
      status: "in_progress",
    })

    await act(async () => {
      mutation.resolve(updated)
      await mutation.promise
    })
    await waitFor(() => expect(status).not.toBeDisabled())
    expect(status).toHaveValue("in_progress")
  })

  it("子任务状态更新失败会回滚并显示局部错误", async () => {
    const root = makeTask("root", { serial: 18 })
    const child = makeTask("child", {
      serial: 19,
      title: "回滚任务",
      parent_id: root.id,
    })
    const mutation = deferred<Task>()
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root, [child])],
      next_cursor: null,
    })
    tasks.updateTask.mockReturnValueOnce(mutation.promise)
    const user = userEvent.setup()
    renderWorkspace()
    const status = await screen.findByRole("combobox", {
      name: "设置 #19 的状态",
    })

    await user.selectOptions(status, "completed")
    expect(status).toHaveValue("completed")
    expect(status).toBeDisabled()

    await act(async () => {
      mutation.reject(new Error("网络中断"))
      await expect(mutation.promise).rejects.toThrow()
    })
    await waitFor(() => expect(status).toHaveValue("new"))
    expect(status).not.toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("任务状态更新失败")
  })

  it("首次失败提供重试并在成功后显示筛选空状态", async () => {
    tasks.listTasks
      .mockRejectedValueOnce(new Error("secret database detail"))
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    const user = userEvent.setup()
    renderWorkspace()

    expect(await screen.findByRole("alert")).toHaveTextContent("任务加载失败")
    await user.click(screen.getByRole("button", { name: "重新加载" }))

    expect(
      await screen.findByText("还没有任务，先写下第一件事。")
    ).toBeInTheDocument()
  })
})

describe("Task4 列表契约", () => {
  it("首次加载继续提供可读状态提示", () => {
    render(
      <TaskList
        groups={[]}
        status="all"
        timeZone="Asia/Shanghai"
        initialLoading
        loadingMore={false}
        nextCursor={null}
        error={null}
        statusError={null}
        statusMutatingTaskIds={new Set()}
        onRetry={vi.fn()}
        onLoadMore={vi.fn()}
        onSelect={vi.fn()}
        onStatusChange={vi.fn()}
      />
    )

    expect(screen.getByRole("status")).toHaveTextContent("正在加载任务")
  })

  it.each([
    ["all", "还没有任务，先写下第一件事。"],
    ["new", "还没有新任务。"],
    ["in_progress", "没有进行中的任务。"],
    ["completed", "还没有已完成的任务。"],
  ] satisfies [TaskStatusFilter, string][])(
    "%s 状态显示专属空文案",
    (status, message) => {
      render(
        <TaskList
          groups={[]}
          status={status}
          timeZone="Asia/Shanghai"
          initialLoading={false}
          loadingMore={false}
          nextCursor={null}
          error={null}
          statusError={null}
          statusMutatingTaskIds={new Set()}
          onRetry={vi.fn()}
          onLoadMore={vi.fn()}
          onSelect={vi.fn()}
          onStatusChange={vi.fn()}
        />
      )

      expect(screen.getByText(message)).toBeInTheDocument()
    }
  )
})

describe("Task3 筛选组件", () => {
  it("提供桌面筛选语义、完整回调、清除筛选和主题重试", async () => {
    const onStatusChange = vi.fn()
    const onTopicChange = vi.fn()
    const onSortChange = vi.fn()
    const onOrderChange = vi.fn()
    const onRetryTopics = vi.fn().mockResolvedValue(undefined)
    const onReset = vi.fn()
    const user = userEvent.setup()

    render(
      <FilterTestHarness
        topicError="主题加载失败"
        onStatusChange={onStatusChange}
        onTopicChange={onTopicChange}
        onSortChange={onSortChange}
        onOrderChange={onOrderChange}
        onRetryTopics={onRetryTopics}
        onReset={onReset}
      />
    )

    const sidebar = screen.getByRole("complementary", { name: "任务筛选" })
    expect(screen.getByRole("main", { name: "Todo List" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "New" })).toHaveAttribute(
      "aria-pressed",
      "false"
    )
    expect(screen.getByRole("alert")).toHaveTextContent("主题加载失败")

    await user.click(screen.getByRole("button", { name: "In Progress" }))
    await user.click(screen.getByRole("button", { name: "Tickly" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "排序字段" }),
      "priority"
    )
    await user.selectOptions(
      screen.getByRole("combobox", { name: "排序顺序" }),
      "asc"
    )
    await user.click(screen.getByRole("button", { name: "重试主题" }))
    await user.click(screen.getByRole("button", { name: "清除筛选" }))

    expect(sidebar).toBeInTheDocument()
    expect(onStatusChange).toHaveBeenCalledOnce()
    expect(onStatusChange).toHaveBeenCalledWith("in_progress")
    expect(onTopicChange).toHaveBeenCalledWith("Tickly")
    expect(onSortChange).toHaveBeenCalledWith("priority")
    expect(onOrderChange).toHaveBeenCalledWith("asc")
    expect(onRetryTopics).toHaveBeenCalledOnce()
    expect(onReset).toHaveBeenCalledOnce()
  })

  it("禁用状态会作用于桌面全部筛选控件", () => {
    render(<FilterTestHarness disabled />)

    expect(screen.getByRole("button", { name: "全部" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Completed" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "全部主题" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "工作" })).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "排序字段" })).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "排序顺序" })).toBeDisabled()
  })

  it("桌面主题加载只禁用主题控件并保留状态与排序操作", () => {
    render(<FilterTestHarness topicLoading />)

    expect(screen.getByRole("button", { name: "全部主题" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "工作" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "New" })).not.toBeDisabled()
    expect(
      screen.getByRole("combobox", { name: "排序字段" })
    ).not.toBeDisabled()
    expect(
      screen.getByRole("combobox", { name: "排序顺序" })
    ).not.toBeDisabled()
  })

  it("移动筛选先保留草稿，再一次应用完整查询并关闭", async () => {
    const onApply = vi.fn()
    const user = userEvent.setup()
    render(<FilterTestHarness onApply={onApply} />)

    await user.click(screen.getByRole("button", { name: "筛选" }))
    const dialog = screen.getByRole("dialog", { name: "筛选与排序" })
    await user.click(screen.getByRole("button", { name: "In Progress" }))
    await user.click(screen.getByRole("button", { name: "Tickly" }))

    expect(dialog).toBeInTheDocument()
    expect(onApply).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "应用筛选" }))

    expect(onApply).toHaveBeenCalledOnce()
    expect(onApply).toHaveBeenCalledWith({
      ...DEFAULT_TASK_QUERY,
      status: "in_progress",
      topic: "Tickly",
    } satisfies Omit<TaskListQuery, "cursor">)
    expect(
      screen.queryByRole("dialog", { name: "筛选与排序" })
    ).not.toBeInTheDocument()
  })

  it("取消、关闭按钮和 Escape 都丢弃移动草稿并恢复焦点", async () => {
    const onApply = vi.fn()
    const user = userEvent.setup()
    render(<FilterTestHarness onApply={onApply} />)
    const trigger = screen.getByRole("button", { name: "筛选" })

    await user.click(trigger)
    await user.click(screen.getByRole("button", { name: "Completed" }))
    await user.click(screen.getByRole("button", { name: "取消" }))
    expect(onApply).not.toHaveBeenCalled()

    await user.click(trigger)
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
    await user.click(screen.getByRole("button", { name: "关闭筛选" }))
    expect(onApply).not.toHaveBeenCalled()

    await user.click(trigger)
    await user.keyboard("{Escape}")
    expect(
      screen.queryByRole("dialog", { name: "筛选与排序" })
    ).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(onApply).not.toHaveBeenCalled()
  })

  it("query 属性变化后重新打开会使用最新草稿", async () => {
    const onApply = vi.fn()
    const user = userEvent.setup()
    const { rerender } = render(<FilterTestHarness onApply={onApply} />)

    await user.click(screen.getByRole("button", { name: "筛选" }))
    await user.click(screen.getByRole("button", { name: "In Progress" }))
    await user.keyboard("{Escape}")

    const latestQuery: WorkspaceQuery = {
      ...DEFAULT_TASK_QUERY,
      status: "completed",
      topic: "工作",
      sort: "priority",
      order: "asc",
    }
    rerender(<FilterTestHarness query={latestQuery} onApply={onApply} />)

    await user.click(screen.getByRole("button", { name: "筛选" }))
    expect(screen.getByRole("button", { name: "Completed" })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
    expect(screen.getByRole("button", { name: "工作" })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
    expect(screen.getByRole("combobox", { name: "排序字段" })).toHaveValue(
      "priority"
    )
    expect(screen.getByRole("combobox", { name: "排序顺序" })).toHaveValue(
      "asc"
    )
  })
})
