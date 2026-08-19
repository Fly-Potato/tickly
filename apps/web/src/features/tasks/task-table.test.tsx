import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import type { Task, TaskGroup } from "./task-api"
import { TaskList } from "./task-list"

function makeTask(
  id: string,
  serial: number,
  overrides: Partial<Task> = {}
): Task {
  return {
    id,
    serial,
    title: `任务 ${serial}`,
    description: `任务 ${serial}`,
    priority: null,
    topic: "Tickly",
    status: "new",
    due_at: null,
    completed_at: null,
    parent_id: null,
    created_at: "2026-08-18T08:00:00Z",
    updated_at: "2026-08-18T08:00:00Z",
    ...overrides,
  }
}

function renderList(
  groups: TaskGroup[],
  overrides: Partial<Parameters<typeof TaskList>[0]> = {}
) {
  const props: Parameters<typeof TaskList>[0] = {
    groups,
    status: "all",
    timeZone: "Asia/Shanghai",
    initialLoading: false,
    loadingMore: false,
    nextCursor: null,
    error: null,
    statusError: null,
    statusMutatingTaskIds: new Set(),
    onRetry: vi.fn().mockResolvedValue(undefined),
    onLoadMore: vi.fn().mockResolvedValue(undefined),
    onSelect: vi.fn(),
    onStatusChange: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
  render(<TaskList {...props} />)
  return props
}

describe("Todo 表格式列表", () => {
  it("展示弱化六列表头和父子任务行", () => {
    const root = makeTask("root", 42, {
      title: "完成表格化",
      priority: "medium",
    })
    const child = makeTask("child", 43, {
      title: "补充移动端布局",
      parent_id: root.id,
      status: "completed",
      completed_at: "2026-08-18T09:00:00Z",
    })
    renderList([
      {
        task: root,
        children: [child],
        child_count: 2,
        completed_child_count: 1,
        context_only: false,
      },
    ])

    expect(screen.getByRole("table", { name: "Todo List" })).toBeInTheDocument()
    expect(
      screen.getAllByRole("columnheader").map((header) => header.textContent)
    ).toEqual(["#", "待办", "主题", "优先级", "截止时间", "状态"])
    expect(screen.getByText("1/2 已完成")).toBeInTheDocument()
    expect(screen.getByRole("row", { name: /补充移动端布局/ })).toHaveAttribute(
      "data-child",
      "true"
    )
    expect(screen.queryByText(/创建 ·/)).not.toBeInTheDocument()
    expect(screen.queryByText(/完成 ·/)).not.toBeInTheDocument()
  })

  it("无优先级和截止时间显示占位值", () => {
    const task = makeTask("plain", 7)
    renderList([
      {
        task,
        children: [],
        child_count: 0,
        completed_child_count: 0,
        context_only: false,
      },
    ])

    const row = screen.getByRole("row", { name: /任务 7/ })
    expect(within(row).getAllByText("—")).toHaveLength(2)
  })

  it("上下文提示留在根任务行且标题按钮打开编辑", async () => {
    const user = userEvent.setup()
    const task = makeTask("context", 9, { title: "上下文父任务" })
    const props = renderList([
      {
        task,
        children: [],
        child_count: 1,
        completed_child_count: 0,
        context_only: true,
      },
    ])

    const row = screen.getByRole("row", { name: /上下文父任务/ })
    expect(within(row).getByText("仅用于展示匹配的子待办")).toBeInTheDocument()
    await user.click(
      within(row).getByRole("button", { name: "编辑 上下文父任务" })
    )
    expect(props.onSelect).toHaveBeenCalledWith(task)
  })

  it("状态下拉保持独立并提交目标状态", async () => {
    const user = userEvent.setup()
    const task = makeTask("status", 12)
    const props = renderList([
      {
        task,
        children: [],
        child_count: 0,
        completed_child_count: 0,
        context_only: false,
      },
    ])

    await user.selectOptions(
      screen.getByLabelText("设置 #12 的状态"),
      "in_progress"
    )
    expect(props.onStatusChange).toHaveBeenCalledWith(task, "in_progress")
    expect(props.onSelect).not.toHaveBeenCalled()
  })

  it("空数据和加载状态继续提供可读提示", () => {
    const { unmount } = render(
      <TaskList {...renderListProps()} groups={[]} initialLoading />
    )
    expect(screen.getByRole("status")).toHaveTextContent("正在加载任务")
    unmount()

    render(<TaskList {...renderListProps()} groups={[]} />)
    expect(screen.getByText("还没有任务，先写下第一件事。")).toBeInTheDocument()
  })
})

function renderListProps(): Parameters<typeof TaskList>[0] {
  return {
    groups: [],
    status: "all",
    timeZone: "Asia/Shanghai",
    initialLoading: false,
    loadingMore: false,
    nextCursor: null,
    error: null,
    statusError: null,
    statusMutatingTaskIds: new Set(),
    onRetry: vi.fn().mockResolvedValue(undefined),
    onLoadMore: vi.fn().mockResolvedValue(undefined),
    onSelect: vi.fn(),
    onStatusChange: vi.fn().mockResolvedValue(undefined),
  }
}
