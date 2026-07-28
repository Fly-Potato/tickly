import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Task, TaskPage } from "./task-api"

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

import { useTaskWorkspace } from "./use-task-workspace"

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

beforeEach(() => {
  Object.values(tasks).forEach((mock) => mock.mockReset())
})

describe("Todo 工作区读取状态", () => {
  it("query 变化会取消旧请求且旧响应不能覆盖新结果", async () => {
    const first = deferred<TaskPage>()
    const second = deferred<TaskPage>()
    tasks.listTasks
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    const staleTask = makeTask("stale")
    const activeTask = makeTask("active")
    const { result } = renderHook(() => useTaskWorkspace())

    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(1))
    const firstSignal = tasks.listTasks.mock.calls[0][1] as AbortSignal
    act(() => result.current.actions.setStatus("active"))
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(2))

    await act(async () => {
      second.resolve({ items: [activeTask], next_cursor: "cursor-2" })
      await second.promise
    })
    expect(result.current.state.items).toEqual([activeTask])
    expect(firstSignal.aborted).toBe(true)

    await act(async () => {
      first.resolve({ items: [staleTask], next_cursor: null })
      await first.promise
    })
    expect(result.current.state.items).toEqual([activeTask])
  })

  it("加载更多按 ID 去重并在末页清空 cursor", async () => {
    const firstTask = makeTask("first")
    const nextTask = makeTask("next")
    tasks.listTasks
      .mockResolvedValueOnce({ items: [firstTask], next_cursor: "cursor-2" })
      .mockResolvedValueOnce({
        items: [firstTask, nextTask],
        next_cursor: null,
      })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.items).toEqual([firstTask]))

    await act(async () => result.current.actions.loadMore())

    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "cursor-2" }),
      expect.any(AbortSignal),
    )
    expect(result.current.state.items.map((task) => task.id)).toEqual([
      "first",
      "next",
    ])
    expect(result.current.state.nextCursor).toBeNull()
  })

  it("加载更多失败保留已有页和 cursor 并可重试", async () => {
    const firstTask = makeTask("first")
    const nextTask = makeTask("next")
    tasks.listTasks
      .mockResolvedValueOnce({ items: [firstTask], next_cursor: "cursor-2" })
      .mockRejectedValueOnce(new Error("secret network detail"))
      .mockResolvedValueOnce({ items: [nextTask], next_cursor: null })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.items).toEqual([firstTask]))

    await act(async () => result.current.actions.loadMore())
    expect(result.current.state.items).toEqual([firstTask])
    expect(result.current.state.nextCursor).toBe("cursor-2")
    expect(result.current.state.error).toBe("任务加载失败")

    await act(async () => result.current.actions.retry())
    expect(result.current.state.items.map((task) => task.id)).toEqual([
      "first",
      "next",
    ])
  })

  it("首次失败可重试且新结果会关闭已不在列表的选择", async () => {
    const selected = makeTask("selected")
    tasks.listTasks
      .mockRejectedValueOnce(new Error("secret database detail"))
      .mockResolvedValueOnce({ items: [selected], next_cursor: null })
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.error).toBe("任务加载失败"))
    expect(result.current.state.error).not.toContain("secret")

    await act(async () => result.current.actions.retry())
    act(() => result.current.actions.selectTask(selected.id))
    expect(result.current.state.selectedTaskId).toBe(selected.id)

    act(() => result.current.actions.setSort("priority"))
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    expect(result.current.state.selectedTaskId).toBeNull()
  })
})
