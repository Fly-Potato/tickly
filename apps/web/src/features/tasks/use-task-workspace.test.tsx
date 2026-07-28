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

describe("Todo 工作区 mutation", () => {
  it("快速新增会规范标题、阻止重复请求并刷新当前 query", async () => {
    const createdTask = makeTask("created", { title: "新任务" })
    const creation = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({ items: [createdTask], next_cursor: null })
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let firstRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.actions.create("  新任务  ")
      void result.current.actions.create("重复请求")
    })

    expect(tasks.createTask).toHaveBeenCalledOnce()
    expect(tasks.createTask).toHaveBeenCalledWith({ title: "新任务" })
    expect(result.current.state.creating).toBe(true)
    await act(async () => {
      creation.resolve(createdTask)
      await firstRequest
    })
    expect(tasks.listTasks).toHaveBeenCalledTimes(2)
    expect(result.current.state.items).toEqual([createdTask])
    expect(result.current.state.creating).toBe(false)
  })

  it("保存成功关闭选择并刷新，失败则保留选择", async () => {
    const original = makeTask("editable")
    const updated = { ...original, title: "已更新" }
    tasks.listTasks
      .mockResolvedValueOnce({ items: [original], next_cursor: null })
      .mockResolvedValueOnce({ items: [updated], next_cursor: null })
    tasks.updateTask.mockResolvedValueOnce(updated)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.items).toEqual([original]))
    act(() => result.current.actions.selectTask(original.id))

    await act(async () => {
      await result.current.actions.save(original.id, { title: "已更新" })
    })

    expect(tasks.updateTask).toHaveBeenCalledWith(original.id, {
      title: "已更新",
    })
    expect(result.current.state.selectedTaskId).toBeNull()
    expect(result.current.state.items).toEqual([updated])

    act(() => result.current.actions.selectTask(updated.id))
    tasks.updateTask.mockRejectedValueOnce(new Error("secret"))
    await act(async () => {
      await expect(
        result.current.actions.save(updated.id, { notes: "保留表单" }),
      ).rejects.toThrow()
    })
    expect(result.current.state.selectedTaskId).toBe(updated.id)
    expect(result.current.state.saving).toBe(false)
  })

  it("完成失败会在同一 query 精确恢复原任务和位置", async () => {
    const first = makeTask("first")
    const target = makeTask("target")
    const failure = deferred<Task>()
    tasks.listTasks.mockResolvedValueOnce({
      items: [first, target],
      next_cursor: null,
    })
    tasks.listTasks.mockResolvedValueOnce({
      items: [first, target],
      next_cursor: null,
    })
    tasks.updateTask.mockReturnValueOnce(failure.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() =>
      expect(result.current.state.items).toEqual([first, target]),
    )
    act(() => result.current.actions.setStatus("active"))
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let request!: Promise<void>
    act(() => {
      request = result.current.actions.setCompleted(target, true)
    })
    expect(result.current.state.items).toEqual([first])
    expect(result.current.state.completingTaskIds.has(target.id)).toBe(true)

    await act(async () => {
      failure.reject(new Error("网络中断"))
      await expect(request).rejects.toThrow()
    })
    expect(result.current.state.items).toEqual([first, target])
    expect(result.current.state.completionError).toBe("完成状态更新失败")
    expect(result.current.state.completingTaskIds.has(target.id)).toBe(false)
  })

  it("query 已变化时完成失败不注入旧快照并重载新 query", async () => {
    const target = makeTask("target")
    const completed = makeTask("completed", { is_completed: true })
    const failure = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [target], next_cursor: null })
      .mockResolvedValueOnce({ items: [completed], next_cursor: null })
      .mockResolvedValueOnce({ items: [completed], next_cursor: null })
    tasks.updateTask.mockReturnValueOnce(failure.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.items).toEqual([target]))

    let request!: Promise<void>
    act(() => {
      request = result.current.actions.setCompleted(target, true)
    })
    act(() => result.current.actions.setStatus("completed"))
    await waitFor(() => expect(result.current.state.items).toEqual([completed]))

    await act(async () => {
      failure.reject(new Error("网络中断"))
      await expect(request).rejects.toThrow()
    })
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(3))
    expect(result.current.state.items).toEqual([completed])
    expect(result.current.state.items).not.toContainEqual(target)
    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "completed" }),
      expect.any(AbortSignal),
    )
  })

  it("删除成功移除任务，删除失败保留编辑上下文", async () => {
    const target = makeTask("target")
    tasks.listTasks.mockResolvedValueOnce({
      items: [target],
      next_cursor: null,
    })
    tasks.deleteTask.mockResolvedValueOnce(undefined)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.items).toEqual([target]))
    act(() => result.current.actions.selectTask(target.id))

    await act(async () => result.current.actions.remove(target.id))
    expect(result.current.state.items).toEqual([])
    expect(result.current.state.selectedTaskId).toBeNull()

    act(() => result.current.actions.selectTask(target.id))
    tasks.deleteTask.mockRejectedValueOnce(new Error("secret"))
    await act(async () => {
      await expect(result.current.actions.remove(target.id)).rejects.toThrow()
    })
    expect(result.current.state.selectedTaskId).toBe(target.id)
    expect(result.current.state.deleting).toBe(false)
  })
})
