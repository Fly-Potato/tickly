import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Task } from "./task-api"

const tasks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  deleteTask: vi.fn(),
}))

vi.mock("./task-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./task-api")>()),
  ...tasks,
}))

import { TodoWorkspace } from "./todo-workspace"

function makeTask(id: string, overrides: Partial<Task> = {}): Task {
  return {
    id,
    title: `任务 ${id}`,
    notes: null,
    is_completed: false,
    priority: "none",
    due_at: null,
    completed_at: null,
    created_at: "2026-07-28T08:00:00Z",
    updated_at: "2026-07-28T08:00:00Z",
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
    />,
  )
  return { onLogout }
}

beforeEach(() => {
  Object.values(tasks).forEach((mock) => mock.mockReset())
})

describe("Todo 工作区", () => {
  it("展示账号、任务语义和退出入口，任务主体打开编辑面板", async () => {
    const task = makeTask("one", {
      title: "完成阶段 4",
      priority: "high",
    })
    tasks.listTasks.mockResolvedValue({ items: [task], next_cursor: null })
    const user = userEvent.setup()
    const { onLogout } = renderWorkspace()

    expect(
      await screen.findByRole("heading", { name: "今天要完成什么？" }),
    ).toBeInTheDocument()
    expect(screen.getByText("potato")).toBeInTheDocument()
    expect(screen.getByText("Asia/Shanghai")).toBeInTheDocument()
    expect(screen.getByText("高优先级")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "退出登录" }))
    expect(onLogout).toHaveBeenCalledOnce()

    await user.click(screen.getByRole("button", { name: "编辑 完成阶段 4" }))
    expect(screen.getByRole("heading", { name: "编辑任务" })).toBeInTheDocument()
  })

  it("Enter 快速新增期间禁用输入，成功后清空并刷新", async () => {
    const created = makeTask("created", { title: "阶段 4" })
    const creation = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({ items: [created], next_cursor: null })
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const user = userEvent.setup()
    renderWorkspace()
    const input = await screen.findByLabelText("任务标题")

    await user.type(input, "阶段 4{Enter}")

    expect(tasks.createTask).toHaveBeenCalledWith({ title: "阶段 4" })
    expect(input).toBeDisabled()
    await act(async () => {
      creation.resolve(created)
      await creation.promise
    })
    await waitFor(() => expect(input).toHaveValue(""))
    expect(await screen.findByText("阶段 4")).toBeInTheDocument()
  })

  it("创建失败保留标题并显示安全错误", async () => {
    tasks.listTasks.mockResolvedValue({ items: [], next_cursor: null })
    tasks.createTask.mockRejectedValue(new Error("secret network detail"))
    const user = userEvent.setup()
    renderWorkspace()
    const input = await screen.findByLabelText("任务标题")

    await user.type(input, "保留标题{Enter}")

    expect(await screen.findByRole("alert")).toHaveTextContent("任务创建失败")
    expect(input).toHaveValue("保留标题")
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument()
  })

  it("筛选后使用当前 cursor 加载更多", async () => {
    const first = makeTask("first")
    const next = makeTask("next")
    tasks.listTasks
      .mockResolvedValueOnce({ items: [first], next_cursor: "initial-cursor" })
      .mockResolvedValueOnce({ items: [first], next_cursor: "active-cursor" })
      .mockResolvedValueOnce({ items: [next], next_cursor: null })
    const user = userEvent.setup()
    renderWorkspace()
    await screen.findByText("任务 first")

    await user.click(screen.getByRole("button", { name: "进行中" }))
    await waitFor(() =>
      expect(tasks.listTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "active" }),
        expect.any(AbortSignal),
      ),
    )
    await user.click(await screen.findByRole("button", { name: "加载更多" }))

    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "active", cursor: "active-cursor" }),
      expect.any(AbortSignal),
    )
    expect(await screen.findByText("任务 next")).toBeInTheDocument()
  })

  it("完成切换失败会恢复 Active 列表并显示局部错误", async () => {
    const target = makeTask("target", { title: "回滚任务" })
    const completion = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [target], next_cursor: null })
      .mockResolvedValueOnce({ items: [target], next_cursor: null })
    tasks.updateTask.mockReturnValueOnce(completion.promise)
    const user = userEvent.setup()
    renderWorkspace()
    await screen.findByText("回滚任务")
    await user.click(screen.getByRole("button", { name: "进行中" }))
    await screen.findByText("回滚任务")

    await user.click(
      screen.getByRole("checkbox", { name: "将回滚任务标记为已完成" }),
    )
    expect(screen.queryByText("回滚任务")).not.toBeInTheDocument()

    await act(async () => {
      completion.reject(new Error("网络中断"))
      await expect(completion.promise).rejects.toThrow()
    })
    expect(await screen.findByText("回滚任务")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("完成状态更新失败")
  })

  it("首次失败提供重试并在成功后显示筛选空状态", async () => {
    tasks.listTasks
      .mockRejectedValueOnce(new Error("secret database detail"))
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    const user = userEvent.setup()
    renderWorkspace()

    expect(await screen.findByRole("alert")).toHaveTextContent("任务加载失败")
    await user.click(screen.getByRole("button", { name: "重新加载" }))

    expect(await screen.findByText("还没有任务，先写下第一件事。"))
      .toBeInTheDocument()
  })
})
