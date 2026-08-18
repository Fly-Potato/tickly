import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  DEFAULT_TASK_QUERY,
  type Task,
  type TaskGroup,
  type TaskPage,
} from "./task-api"

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

import { findTaskInGroups, useTaskWorkspace } from "./use-task-workspace"

function makeTask(
  id: string,
  serial: number,
  overrides: Partial<Task> = {}
): Task {
  return {
    id,
    serial,
    title: `任务 ${id}`,
    description: `任务 ${id}`,
    priority: null,
    topic: "Tickly",
    status: "new",
    due_at: null,
    completed_at: null,
    parent_id: null,
    created_at: "2026-08-17T08:00:00Z",
    updated_at: "2026-08-17T08:00:00Z",
    ...overrides,
  }
}

function makeGroup(task: Task, children: Task[] = []): TaskGroup {
  return {
    task,
    children,
    child_count: children.length,
    completed_child_count: children.filter(
      (child) => child.status === "completed"
    ).length,
    context_only: false,
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
  tasks.listTaskTopics.mockResolvedValue([])
})

describe("Todo 工作区读取状态", () => {
  it("挂载时并行启动任务列表和主题请求", async () => {
    const page = deferred<TaskPage>()
    const topics = deferred<string[]>()
    tasks.listTasks.mockReturnValueOnce(page.promise)
    tasks.listTaskTopics.mockReturnValueOnce(topics.promise)

    const { result } = renderHook(() => useTaskWorkspace())

    await waitFor(() => {
      expect(tasks.listTasks).toHaveBeenCalledWith(
        DEFAULT_TASK_QUERY,
        expect.any(AbortSignal)
      )
      expect(tasks.listTaskTopics).toHaveBeenCalledOnce()
    })
    expect(result.current.state.initialLoading).toBe(true)
    expect(result.current.state.topicLoading).toBe(true)

    await act(async () => {
      page.resolve({ items: [], next_cursor: null })
      topics.resolve(["Tickly", "工作"])
      await Promise.all([page.promise, topics.promise])
    })
    expect(result.current.state.topics).toEqual(["Tickly", "工作"])
  })

  it("主题失败使用独立错误域并可单独重试", async () => {
    const root = makeTask("root", 1)
    tasks.listTasks.mockResolvedValueOnce({
      items: [makeGroup(root)],
      next_cursor: null,
    })
    tasks.listTaskTopics
      .mockRejectedValueOnce(new Error("secret topic detail"))
      .mockResolvedValueOnce(["Tickly"])

    const { result } = renderHook(() => useTaskWorkspace())

    await waitFor(() => expect(result.current.state.topicLoading).toBe(false))
    expect(result.current.state.items).toEqual([makeGroup(root)])
    expect(result.current.state.error).toBeNull()
    expect(result.current.state.topicError).toBe("主题加载失败")
    expect(result.current.state.topicError).not.toContain("secret")

    await act(async () => result.current.actions.retryTopics())
    expect(result.current.state.topics).toEqual(["Tickly"])
    expect(result.current.state.topicError).toBeNull()
  })

  it("首次列表失败后可重试成功且不暴露底层错误", async () => {
    const rootGroup = makeGroup(makeTask("root", 1))
    tasks.listTasks
      .mockRejectedValueOnce(new Error("secret database detail"))
      .mockResolvedValueOnce({ items: [rootGroup], next_cursor: null })
    const { result } = renderHook(() => useTaskWorkspace())

    await waitFor(() => expect(result.current.state.error).toBe("任务加载失败"))
    expect(result.current.state.error).not.toContain("secret")

    await act(async () => result.current.actions.retry())
    expect(result.current.state.items).toEqual([rootGroup])
    expect(result.current.state.error).toBeNull()
  })

  it("主题 query 变化取消旧请求且相同 query 不重复读取", async () => {
    const first = deferred<TaskPage>()
    const second = deferred<TaskPage>()
    tasks.listTasks
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    const staleGroup = makeGroup(makeTask("stale", 1))
    const ticklyGroup = makeGroup(makeTask("tickly", 2))
    const { result } = renderHook(() => useTaskWorkspace())

    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledOnce())
    const firstSignal = tasks.listTasks.mock.calls[0][1] as AbortSignal
    act(() => result.current.actions.setTopic("Tickly"))
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(2))
    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      { ...DEFAULT_TASK_QUERY, topic: "Tickly" },
      expect.any(AbortSignal)
    )
    expect(firstSignal.aborted).toBe(true)

    act(() => {
      result.current.actions.setTopic("Tickly")
      result.current.actions.applyQuery({
        ...DEFAULT_TASK_QUERY,
        topic: "Tickly",
      })
    })
    expect(tasks.listTasks).toHaveBeenCalledTimes(2)

    await act(async () => {
      second.resolve({ items: [ticklyGroup], next_cursor: null })
      await second.promise
    })
    await act(async () => {
      first.resolve({ items: [staleGroup], next_cursor: null })
      await first.promise
    })
    expect(result.current.state.items).toEqual([ticklyGroup])
  })

  it("一次应用组合 query 只发起一个最新列表请求", async () => {
    tasks.listTasks.mockResolvedValue({ items: [], next_cursor: null })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledOnce())

    act(() =>
      result.current.actions.applyQuery({
        status: "in_progress",
        topic: "工作",
        sort: "serial",
        order: "asc",
        limit: 50,
      })
    )

    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(2))
    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      {
        status: "in_progress",
        topic: "工作",
        sort: "serial",
        order: "asc",
        limit: 50,
      },
      expect.any(AbortSignal)
    )
  })

  it("加载更多只按根任务 ID 去重而不按子任务 ID 去重分组", async () => {
    const sharedChild = makeTask("shared-child", 2, { parent_id: "root-1" })
    const firstGroup = makeGroup(makeTask("root-1", 1), [sharedChild])
    const duplicateRoot = makeGroup(makeTask("root-1", 1, { title: "重复根" }))
    const secondGroup = makeGroup(makeTask("root-2", 3), [
      { ...sharedChild, parent_id: "root-2" },
    ])
    tasks.listTasks
      .mockResolvedValueOnce({ items: [firstGroup], next_cursor: "cursor-2" })
      .mockResolvedValueOnce({
        items: [duplicateRoot, secondGroup, secondGroup],
        next_cursor: null,
      })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() =>
      expect(result.current.state.items).toEqual([firstGroup])
    )

    await act(async () => result.current.actions.loadMore())

    expect(result.current.state.items.map((group) => group.task.id)).toEqual([
      "root-1",
      "root-2",
    ])
    expect(result.current.state.items[1].children[0].id).toBe("shared-child")
    expect(result.current.state.nextCursor).toBeNull()
  })

  it("加载更多失败保留已有分组和 cursor，并可从同一 cursor 重试", async () => {
    const firstGroup = makeGroup(makeTask("root-1", 1))
    const nextGroup = makeGroup(makeTask("root-2", 2))
    tasks.listTasks
      .mockResolvedValueOnce({ items: [firstGroup], next_cursor: "cursor-2" })
      .mockRejectedValueOnce(new Error("secret page detail"))
      .mockResolvedValueOnce({ items: [nextGroup], next_cursor: null })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() =>
      expect(result.current.state.items).toEqual([firstGroup])
    )

    await act(async () => result.current.actions.loadMore())
    expect(result.current.state.items).toEqual([firstGroup])
    expect(result.current.state.nextCursor).toBe("cursor-2")
    expect(result.current.state.error).toBe("任务加载失败")

    await act(async () => result.current.actions.retry())
    expect(tasks.listTasks).toHaveBeenLastCalledWith(
      { ...DEFAULT_TASK_QUERY, cursor: "cursor-2" },
      expect.any(AbortSignal)
    )
    expect(result.current.state.items).toEqual([firstGroup, nextGroup])
    expect(result.current.state.nextCursor).toBeNull()
  })

  it("过期加载更多与重复触发都不能污染新 query", async () => {
    const firstGroup = makeGroup(makeTask("root-1", 1))
    const staleGroup = makeGroup(makeTask("stale", 2))
    const currentGroup = makeGroup(makeTask("current", 3))
    const more = deferred<TaskPage>()
    const current = deferred<TaskPage>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [firstGroup], next_cursor: "cursor-2" })
      .mockReturnValueOnce(more.promise)
      .mockReturnValueOnce(current.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() =>
      expect(result.current.state.items).toEqual([firstGroup])
    )

    act(() => {
      void result.current.actions.loadMore()
      void result.current.actions.loadMore()
    })
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(2))
    act(() => result.current.actions.setStatus("completed"))
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(3))

    await act(async () => {
      current.resolve({ items: [currentGroup], next_cursor: null })
      await current.promise
    })
    await act(async () => {
      more.resolve({ items: [staleGroup], next_cursor: null })
      await more.promise
    })
    expect(result.current.state.items).toEqual([currentGroup])
  })

  it("按稳定 ID 查找根任务和子任务", () => {
    const child = makeTask("child", 2, { parent_id: "root" })
    const root = makeTask("root", 1)
    const groups = [makeGroup(root, [child])]

    expect(findTaskInGroups(groups, root.id)).toBe(root)
    expect(findTaskInGroups(groups, child.id)).toBe(child)
    expect(findTaskInGroups(groups, "missing")).toBeNull()
  })

  it.each([
    ["根任务", "root"],
    ["子任务", "child"],
  ])("刷新时保留仍存在的%s选择，并清理新结果中缺失的选择", async (_, id) => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const group = makeGroup(root, [child])
    tasks.listTasks
      .mockResolvedValueOnce({ items: [group], next_cursor: null })
      .mockResolvedValueOnce({ items: [group], next_cursor: null })
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.items).toEqual([group]))
    act(() => result.current.actions.selectTask(id))

    await act(async () => result.current.actions.retry())
    expect(result.current.state.selectedTaskId).toBe(id)

    act(() => result.current.actions.setTopic("其他主题"))
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    expect(result.current.state.selectedTaskId).toBeNull()
  })
})

describe("Todo 工作区 mutation", () => {
  it("创建成功并行刷新当前列表和主题，失败会抛错并复位", async () => {
    const created = makeTask("created", 1)
    const creation = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({ items: [makeGroup(created)], next_cursor: null })
    tasks.listTaskTopics
      .mockResolvedValueOnce(["Tickly"])
      .mockResolvedValueOnce(["Tickly", "工作"])
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    const input = { title: "新任务", topic: "Tickly" }
    let request!: Promise<void>
    let duplicateRequest!: Promise<void>
    act(() => {
      request = result.current.actions.create(input)
      duplicateRequest = result.current.actions.create({
        title: "重复",
        topic: "工作",
      })
    })
    await act(async () => {
      await expect(duplicateRequest).rejects.toThrow("已有任务操作正在进行中")
    })
    expect(tasks.createTask).toHaveBeenCalledOnce()
    expect(tasks.createTask).toHaveBeenCalledWith(input)
    expect(result.current.state.creating).toBe(true)

    await act(async () => {
      creation.resolve(created)
      await request
    })
    expect(tasks.listTasks).toHaveBeenCalledTimes(2)
    expect(tasks.listTaskTopics).toHaveBeenCalledTimes(2)
    expect(result.current.state.items).toEqual([makeGroup(created)])
    expect(result.current.state.topics).toEqual(["Tickly", "工作"])
    expect(result.current.state.creating).toBe(false)

    tasks.createTask.mockRejectedValueOnce(new Error("保留调用方表单"))
    await act(async () => {
      await expect(
        result.current.actions.create({ title: "失败任务", topic: "工作" })
      ).rejects.toThrow("保留调用方表单")
    })
    expect(result.current.state.creating).toBe(false)
    expect(tasks.listTasks).toHaveBeenCalledTimes(2)
  })

  it("分页父任务创建子待办后保留已打开分组并沿第一页 cursor 继续去重分页", async () => {
    const firstRoot = makeTask("first", 1)
    const pagedParent = makeTask("paged-parent", 2, { topic: "工作" })
    const pagedGroup = {
      ...makeGroup(pagedParent),
      child_count: 2,
    }
    const createdChild = makeTask("created-child", 3, {
      topic: "工作",
      status: "completed",
      parent_id: pagedParent.id,
    })
    const nextRoot = makeTask("next", 4)
    tasks.listTasks
      .mockResolvedValueOnce({
        items: [makeGroup(firstRoot)],
        next_cursor: "page-2",
      })
      .mockResolvedValueOnce({ items: [pagedGroup], next_cursor: null })
      .mockResolvedValueOnce({
        items: [makeGroup(firstRoot)],
        next_cursor: "page-2",
      })
      .mockResolvedValueOnce({
        items: [
          {
            ...pagedGroup,
            task: { ...pagedParent, title: "重复服务端父任务" },
          },
          makeGroup(nextRoot),
        ],
        next_cursor: null,
      })
    tasks.createTask.mockResolvedValueOnce(createdChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    await act(async () => result.current.actions.loadMore())
    act(() => result.current.actions.selectTask(pagedParent.id))

    await act(async () => {
      await result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: pagedParent.id,
      })
    })

    const retained = result.current.state.items.find(
      (group) => group.task.id === pagedParent.id
    )
    expect(retained).toEqual({
      ...pagedGroup,
      children: [createdChild],
      child_count: 3,
      completed_child_count: 1,
    })
    expect(result.current.state.selectedTaskId).toBe(pagedParent.id)
    expect(result.current.state.nextCursor).toBe("page-2")

    await act(async () => result.current.actions.loadMore())
    expect(result.current.state.items.map((group) => group.task.id)).toEqual([
      firstRoot.id,
      pagedParent.id,
      nextRoot.id,
    ])
    expect(
      result.current.state.items.find(
        (group) => group.task.id === pagedParent.id
      )?.task.title
    ).toBe(pagedParent.title)
  })

  it("子待办创建期间拒绝保存且完成后保留分页父分组", async () => {
    const firstRoot = makeTask("first", 1)
    const pagedParent = makeTask("paged-parent", 2, { topic: "工作" })
    const pagedGroup = {
      ...makeGroup(pagedParent),
      child_count: 2,
    }
    const createdChild = makeTask("created-child", 3, {
      topic: "工作",
      status: "completed",
      parent_id: pagedParent.id,
    })
    const creation = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({
        items: [makeGroup(firstRoot)],
        next_cursor: "page-2",
      })
      .mockResolvedValueOnce({ items: [pagedGroup], next_cursor: null })
      .mockResolvedValueOnce({
        items: [makeGroup(firstRoot)],
        next_cursor: "page-2",
      })
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    await act(async () => result.current.actions.loadMore())
    act(() => result.current.actions.selectTask(pagedParent.id))

    let createRequest!: Promise<void>
    let saveConflict!: Promise<void>
    act(() => {
      createRequest = result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: pagedParent.id,
      })
      saveConflict = result.current.actions.save(pagedParent.id, {
        title: "禁止并发保存",
      })
    })
    await act(async () => {
      await expect(saveConflict).rejects.toThrow("已有任务操作正在进行中")
    })
    expect(tasks.updateTask).not.toHaveBeenCalled()

    await act(async () => {
      creation.resolve(createdChild)
      await createRequest
    })
    expect(
      result.current.state.items.find(
        (group) => group.task.id === pagedParent.id
      )
    ).toEqual({
      ...pagedGroup,
      children: [createdChild],
      child_count: 3,
      completed_child_count: 1,
    })
    expect(result.current.state.selectedTaskId).toBe(pagedParent.id)
    expect(result.current.state.nextCursor).toBe("page-2")
  })

  it("子待办创建期间拒绝删除且不发送删除请求", async () => {
    const root = makeTask("root", 1)
    const createdChild = makeTask("created-child", 2, {
      parent_id: root.id,
    })
    const creation = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [makeGroup(root)], next_cursor: null })
      .mockResolvedValueOnce({
        items: [makeGroup(root, [createdChild])],
        next_cursor: null,
      })
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let createRequest!: Promise<void>
    let removeConflict!: Promise<void>
    act(() => {
      createRequest = result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: root.id,
      })
      removeConflict = result.current.actions.remove(root.id)
    })
    await act(async () => {
      await expect(removeConflict).rejects.toThrow("已有任务操作正在进行中")
    })
    expect(tasks.deleteTask).not.toHaveBeenCalled()

    await act(async () => {
      creation.resolve(createdChild)
      await createRequest
    })
  })

  it("保存期间拒绝创建且保存失败释放结构操作所有权", async () => {
    const root = makeTask("root", 1)
    const createdChild = makeTask("created-child", 2, {
      parent_id: root.id,
    })
    const saving = deferred<Task>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [makeGroup(root)], next_cursor: null })
      .mockResolvedValueOnce({
        items: [makeGroup(root, [createdChild])],
        next_cursor: null,
      })
    tasks.updateTask.mockReturnValueOnce(saving.promise)
    tasks.createTask.mockResolvedValueOnce(createdChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let saveRequest!: Promise<void>
    let createConflict!: Promise<void>
    act(() => {
      saveRequest = result.current.actions.save(root.id, {
        title: "等待中的保存",
      })
      createConflict = result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: root.id,
      })
    })
    await act(async () => {
      await expect(createConflict).rejects.toThrow("已有任务操作正在进行中")
    })
    expect(tasks.createTask).not.toHaveBeenCalled()
    expect(result.current.state.items[0].child_count).toBe(0)

    await act(async () => {
      saving.reject(new Error("保存失败"))
      await expect(saveRequest).rejects.toThrow("保存失败")
    })
    await act(async () => {
      await result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: root.id,
      })
    })
    expect(tasks.createTask).toHaveBeenCalledOnce()
    expect(result.current.state.items[0].children).toEqual([createdChild])
  })

  it("父根匹配当前 query 时展示新建的不匹配子任务", async () => {
    const parent = makeTask("matching-parent", 10, { topic: "工作" })
    const createdChild = makeTask("other-topic-child", 11, {
      topic: "个人",
      parent_id: parent.id,
    })
    const parentGroup = makeGroup(parent)
    tasks.listTasks
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({ items: [parentGroup], next_cursor: null })
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    tasks.createTask.mockResolvedValueOnce(createdChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    act(() => result.current.actions.setTopic("工作"))
    await waitFor(() =>
      expect(result.current.state.items).toEqual([parentGroup])
    )
    act(() => result.current.actions.selectTask(parent.id))

    await act(async () => {
      await result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: parent.id,
      })
    })

    expect(result.current.state.items).toEqual([
      {
        ...parentGroup,
        children: [createdChild],
        child_count: 1,
      },
    ])
    expect(result.current.state.selectedTaskId).toBe(parent.id)
  })

  it("父根和新子任务均不匹配 query 时只更新总计数", async () => {
    const parent = makeTask("context-parent", 20, { topic: "其他" })
    const visibleChild = makeTask("visible-child", 21, {
      topic: "工作",
      parent_id: parent.id,
    })
    const createdChild = makeTask("hidden-child", 22, {
      topic: "个人",
      parent_id: parent.id,
    })
    const parentGroup = makeGroup(parent, [visibleChild])
    tasks.listTasks
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({ items: [parentGroup], next_cursor: null })
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    tasks.createTask.mockResolvedValueOnce(createdChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    act(() => result.current.actions.setTopic("工作"))
    await waitFor(() =>
      expect(result.current.state.items).toEqual([parentGroup])
    )

    await act(async () => {
      await result.current.actions.create({
        title: createdChild.title,
        topic: createdChild.topic,
        parent_id: parent.id,
      })
    })

    expect(result.current.state.items).toEqual([
      {
        ...parentGroup,
        child_count: 2,
      },
    ])
  })

  it("保存会先替换根或子节点的服务端响应，再重读当前 query", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const originalGroup = makeGroup(root, [child])
    const serverChild = { ...child, title: "服务端子任务" }
    const refreshedGroup = makeGroup(root, [serverChild])
    const refresh = deferred<TaskPage>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [originalGroup], next_cursor: null })
      .mockReturnValueOnce(refresh.promise)
    tasks.updateTask.mockResolvedValueOnce(serverChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() =>
      expect(result.current.state.items).toEqual([originalGroup])
    )
    act(() => result.current.actions.selectTask(child.id))

    let request!: Promise<void>
    act(() => {
      request = result.current.actions.save(child.id, { title: "服务端子任务" })
    })
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(2))
    expect(findTaskInGroups(result.current.state.items, child.id)).toBe(
      serverChild
    )
    expect(result.current.state.selectedTaskId).toBe(child.id)

    await act(async () => {
      refresh.resolve({ items: [refreshedGroup], next_cursor: null })
      await request
    })
    expect(result.current.state.items).toEqual([refreshedGroup])

    const serverRoot = { ...root, title: "服务端根任务" }
    tasks.updateTask.mockResolvedValueOnce(serverRoot)
    tasks.listTasks.mockResolvedValueOnce({
      items: [makeGroup(serverRoot, [serverChild])],
      next_cursor: null,
    })
    await act(async () => {
      await result.current.actions.save(root.id, { title: "服务端根任务" })
    })
    expect(findTaskInGroups(result.current.state.items, root.id)).toEqual(
      serverRoot
    )

    tasks.updateTask.mockRejectedValueOnce(new Error("保存失败"))
    await act(async () => {
      await expect(
        result.current.actions.save(child.id, { description: "保留表单" })
      ).rejects.toThrow("保存失败")
    })
    expect(result.current.state.saving).toBe(false)
    expect(result.current.state.selectedTaskId).toBe(child.id)
    expect(tasks.listTaskTopics).toHaveBeenCalledOnce()
  })

  it("保存主题后并行重读当前列表和主题集合", async () => {
    const root = makeTask("root", 1, { topic: "工作" })
    const updatedRoot = { ...root, topic: "Personal" }
    const refreshedGroup = makeGroup(updatedRoot)
    tasks.listTasks
      .mockResolvedValueOnce({ items: [makeGroup(root)], next_cursor: null })
      .mockResolvedValueOnce({ items: [refreshedGroup], next_cursor: null })
    tasks.listTaskTopics
      .mockResolvedValueOnce(["工作"])
      .mockResolvedValueOnce(["Personal"])
    tasks.updateTask.mockResolvedValueOnce(updatedRoot)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => {
      expect(result.current.state.initialLoading).toBe(false)
      expect(result.current.state.topicLoading).toBe(false)
    })

    await act(async () => {
      await result.current.actions.save(root.id, { topic: "Personal" })
    })

    expect(tasks.updateTask).toHaveBeenCalledWith(root.id, {
      topic: "Personal",
    })
    expect(tasks.listTasks).toHaveBeenCalledTimes(2)
    expect(tasks.listTaskTopics).toHaveBeenCalledTimes(2)
    expect(result.current.state.items).toEqual([refreshedGroup])
    expect(result.current.state.topics).toEqual(["Personal"])
    expect(result.current.state.error).toBeNull()
    expect(result.current.state.topicError).toBeNull()
  })

  it("保存主题后的主题刷新失败只进入主题错误域", async () => {
    const root = makeTask("root", 1, { topic: "工作" })
    const updatedRoot = { ...root, topic: "Personal" }
    const refreshedGroup = makeGroup(updatedRoot)
    tasks.listTasks
      .mockResolvedValueOnce({ items: [makeGroup(root)], next_cursor: null })
      .mockResolvedValueOnce({ items: [refreshedGroup], next_cursor: null })
    tasks.listTaskTopics
      .mockResolvedValueOnce(["工作"])
      .mockRejectedValueOnce(new Error("secret topic refresh detail"))
    tasks.updateTask.mockResolvedValueOnce(updatedRoot)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => {
      expect(result.current.state.initialLoading).toBe(false)
      expect(result.current.state.topicLoading).toBe(false)
    })

    await act(async () => {
      await expect(
        result.current.actions.save(root.id, { topic: "Personal" })
      ).resolves.toBeUndefined()
    })

    expect(result.current.state.items).toEqual([refreshedGroup])
    expect(result.current.state.topics).toEqual(["工作"])
    expect(result.current.state.error).toBeNull()
    expect(result.current.state.topicError).toBe("主题加载失败")
    expect(result.current.state.topicError).not.toContain("secret")
    expect(result.current.state.saving).toBe(false)
  })

  it("子任务状态失败会精确回滚快照并清理 mutation ID", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const failure = deferred<Task>()
    tasks.listTasks.mockResolvedValueOnce({
      items: [makeGroup(root, [child])],
      next_cursor: null,
    })
    tasks.updateTask.mockReturnValueOnce(failure.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let request!: Promise<void>
    act(() => {
      request = result.current.actions.changeStatus(child, "completed")
    })
    expect(findTaskInGroups(result.current.state.items, child.id)).toEqual({
      ...child,
      status: "completed",
      completed_at: child.completed_at,
    })
    expect(result.current.state.items[0].completed_child_count).toBe(1)
    expect(result.current.state.statusMutatingTaskIds.has(child.id)).toBe(true)

    await act(async () => {
      failure.reject(new Error("网络中断"))
      await expect(request).rejects.toThrow("网络中断")
    })
    expect(findTaskInGroups(result.current.state.items, child.id)).toEqual(
      child
    )
    expect(result.current.state.items[0].completed_child_count).toBe(0)
    expect(result.current.state.statusError).toBe("任务状态更新失败")
    expect(result.current.state.statusMutatingTaskIds.has(child.id)).toBe(false)
  })

  it("状态成功先采用服务端节点，再用当前 query 重读分组", async () => {
    const root = makeTask("root", 1, {
      status: "in_progress",
      completed_at: "2026-08-16T08:00:00Z",
    })
    const serverRoot = {
      ...root,
      status: "completed" as const,
      completed_at: "2026-08-18T08:00:00Z",
    }
    const update = deferred<Task>()
    const refresh = deferred<TaskPage>()
    tasks.listTasks
      .mockResolvedValueOnce({ items: [makeGroup(root)], next_cursor: null })
      .mockReturnValueOnce(refresh.promise)
    tasks.updateTask.mockReturnValueOnce(update.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let request!: Promise<void>
    act(() => {
      request = result.current.actions.changeStatus(root, "completed")
    })
    expect(findTaskInGroups(result.current.state.items, root.id)).toEqual({
      ...root,
      status: "completed",
      completed_at: root.completed_at,
    })

    await act(async () => {
      update.resolve(serverRoot)
      await update.promise
    })
    await waitFor(() => expect(tasks.listTasks).toHaveBeenCalledTimes(2))
    expect(findTaskInGroups(result.current.state.items, root.id)).toBe(
      serverRoot
    )

    await act(async () => {
      refresh.resolve({ items: [makeGroup(serverRoot)], next_cursor: null })
      await request
    })
    expect(result.current.state.statusMutatingTaskIds.has(root.id)).toBe(false)
  })

  it("状态回滚只恢复状态字段并保留并发保存成功的标题", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const statusUpdate = deferred<Task>()
    const savedChild = {
      ...child,
      title: "并发保存后的标题",
      status: "completed" as const,
    }
    tasks.listTasks
      .mockResolvedValueOnce({
        items: [makeGroup(root, [child])],
        next_cursor: null,
      })
      .mockResolvedValueOnce({
        items: [makeGroup(root, [savedChild])],
        next_cursor: null,
      })
    tasks.updateTask
      .mockReturnValueOnce(statusUpdate.promise)
      .mockResolvedValueOnce(savedChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let statusRequest!: Promise<void>
    act(() => {
      statusRequest = result.current.actions.changeStatus(child, "completed")
    })
    await act(async () => {
      await result.current.actions.save(child.id, {
        title: "并发保存后的标题",
      })
    })
    expect(findTaskInGroups(result.current.state.items, child.id)).toEqual(
      savedChild
    )
    expect(result.current.state.items[0].completed_child_count).toBe(1)

    await act(async () => {
      statusUpdate.reject(new Error("状态写入失败"))
      await expect(statusRequest).rejects.toThrow("状态写入失败")
    })
    expect(findTaskInGroups(result.current.state.items, child.id)).toEqual({
      ...savedChild,
      status: "new",
      completed_at: null,
    })
    expect(result.current.state.items[0].completed_child_count).toBe(0)
  })

  it("changeStatus 占锁时拒绝同任务的 status save，成功后锁可复用", async () => {
    const root = makeTask("root", 1)
    const statusUpdate = deferred<Task>()
    const completedRoot = { ...root, status: "completed" as const }
    const inProgressRoot = { ...root, status: "in_progress" as const }
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root)],
      next_cursor: null,
    })
    tasks.updateTask
      .mockReturnValueOnce(statusUpdate.promise)
      .mockResolvedValueOnce(inProgressRoot)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let statusRequest!: Promise<void>
    act(() => {
      statusRequest = result.current.actions.changeStatus(root, "completed")
    })
    await act(async () => {
      await expect(
        result.current.actions.save(root.id, { status: "completed" })
      ).rejects.toThrow("任务状态正在更新，请稍后重试")
    })
    expect(tasks.updateTask).toHaveBeenCalledOnce()
    expect(result.current.state.saving).toBe(false)
    expect(result.current.state.statusMutatingTaskIds.has(root.id)).toBe(true)

    await act(async () => {
      statusUpdate.resolve(completedRoot)
      await statusRequest
    })
    expect(result.current.state.statusMutatingTaskIds.has(root.id)).toBe(false)

    await act(async () => {
      await result.current.actions.save(root.id, { status: "in_progress" })
    })
    expect(tasks.updateTask).toHaveBeenCalledTimes(2)
  })

  it("status save 占锁时忽略同任务 changeStatus，失败后锁可复用", async () => {
    const root = makeTask("root", 1)
    const savingStatus = deferred<Task>()
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root)],
      next_cursor: null,
    })
    tasks.updateTask
      .mockReturnValueOnce(savingStatus.promise)
      .mockResolvedValueOnce({ ...root, status: "completed" })
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let saveRequest!: Promise<void>
    act(() => {
      saveRequest = result.current.actions.save(root.id, {
        status: "in_progress",
      })
    })
    expect(result.current.state.statusMutatingTaskIds.has(root.id)).toBe(true)
    await act(async () => {
      await result.current.actions.changeStatus(root, "completed")
    })
    expect(tasks.updateTask).toHaveBeenCalledOnce()

    await act(async () => {
      savingStatus.reject(new Error("保存状态失败"))
      await expect(saveRequest).rejects.toThrow("保存状态失败")
    })
    expect(result.current.state.statusMutatingTaskIds.has(root.id)).toBe(false)

    await act(async () => {
      await result.current.actions.changeStatus(root, "completed")
    })
    expect(tasks.updateTask).toHaveBeenCalledTimes(2)
  })

  it("不同任务的 status save 与 changeStatus 可以并行", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const rootSave = deferred<Task>()
    const childStatus = deferred<Task>()
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root, [child])],
      next_cursor: null,
    })
    tasks.updateTask
      .mockReturnValueOnce(rootSave.promise)
      .mockReturnValueOnce(childStatus.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let saveRequest!: Promise<void>
    let statusRequest!: Promise<void>
    act(() => {
      saveRequest = result.current.actions.save(root.id, {
        status: "in_progress",
      })
      statusRequest = result.current.actions.changeStatus(child, "completed")
    })
    expect(tasks.updateTask).toHaveBeenCalledTimes(2)
    expect([...result.current.state.statusMutatingTaskIds]).toEqual([
      root.id,
      child.id,
    ])

    await act(async () => {
      rootSave.resolve({ ...root, status: "in_progress" })
      childStatus.resolve({ ...child, status: "completed" })
      await Promise.all([saveRequest, statusRequest])
    })
    expect(result.current.state.statusMutatingTaskIds.size).toBe(0)
  })

  it("子任务状态刷新失败时服务端节点和完成计数仍保持一致", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const serverChild = {
      ...child,
      status: "completed" as const,
      completed_at: "2026-08-18T08:00:00Z",
    }
    tasks.listTasks
      .mockResolvedValueOnce({
        items: [makeGroup(root, [child])],
        next_cursor: null,
      })
      .mockRejectedValueOnce(new Error("刷新失败"))
    tasks.updateTask.mockResolvedValueOnce(serverChild)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    await act(async () => {
      await result.current.actions.changeStatus(child, "completed")
    })

    expect(findTaskInGroups(result.current.state.items, child.id)).toBe(
      serverChild
    )
    expect(result.current.state.items[0].completed_child_count).toBe(1)
    expect(result.current.state.error).toBe("任务加载失败")
  })

  it("不同任务状态请求可并行而同一任务重复触发会被忽略", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const rootUpdate = deferred<Task>()
    const childUpdate = deferred<Task>()
    tasks.listTasks.mockResolvedValue({
      items: [makeGroup(root, [child])],
      next_cursor: null,
    })
    tasks.updateTask
      .mockReturnValueOnce(rootUpdate.promise)
      .mockReturnValueOnce(childUpdate.promise)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))

    let rootRequest!: Promise<void>
    let childRequest!: Promise<void>
    act(() => {
      rootRequest = result.current.actions.changeStatus(root, "in_progress")
      childRequest = result.current.actions.changeStatus(child, "completed")
      void result.current.actions.changeStatus(child, "in_progress")
    })
    expect(tasks.updateTask).toHaveBeenCalledTimes(2)
    expect([...result.current.state.statusMutatingTaskIds]).toEqual([
      root.id,
      child.id,
    ])

    await act(async () => {
      childUpdate.reject(new Error("子任务失败"))
      await expect(childRequest).rejects.toThrow("子任务失败")
    })
    expect(result.current.state.statusMutatingTaskIds.has(root.id)).toBe(true)
    expect(result.current.state.statusMutatingTaskIds.has(child.id)).toBe(false)

    await act(async () => {
      rootUpdate.resolve({ ...root, status: "in_progress" })
      await rootRequest
    })
    expect(result.current.state.statusMutatingTaskIds.size).toBe(0)
  })

  it("删除父任务后重读提升的子任务和主题，失败则保留选择", async () => {
    const root = makeTask("root", 1)
    const child = makeTask("child", 2, { parent_id: root.id })
    const elevatedChild = { ...child, parent_id: null }
    tasks.listTasks
      .mockResolvedValueOnce({
        items: [makeGroup(root, [child])],
        next_cursor: null,
      })
      .mockResolvedValueOnce({
        items: [makeGroup(elevatedChild)],
        next_cursor: null,
      })
    tasks.listTaskTopics
      .mockResolvedValueOnce(["Tickly"])
      .mockResolvedValueOnce(["Tickly", "工作"])
    tasks.deleteTask.mockResolvedValueOnce(undefined)
    const { result } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    act(() => result.current.actions.selectTask(root.id))

    await act(async () => result.current.actions.remove(root.id))
    expect(result.current.state.items).toEqual([makeGroup(elevatedChild)])
    expect(result.current.state.selectedTaskId).toBeNull()
    expect(tasks.listTaskTopics).toHaveBeenCalledTimes(2)

    act(() => result.current.actions.selectTask(elevatedChild.id))
    tasks.deleteTask.mockRejectedValueOnce(new Error("删除失败"))
    await act(async () => {
      await expect(
        result.current.actions.remove(elevatedChild.id)
      ).rejects.toThrow("删除失败")
    })
    expect(result.current.state.selectedTaskId).toBe(elevatedChild.id)
    expect(result.current.state.deleting).toBe(false)
  })

  it("创建写请求在卸载后成功不会启动列表或主题刷新", async () => {
    const creation = deferred<Task>()
    tasks.listTasks.mockResolvedValueOnce({ items: [], next_cursor: null })
    tasks.createTask.mockReturnValueOnce(creation.promise)
    const { result, unmount } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    const request = result.current.actions.create({
      title: "卸载中的创建",
      topic: "Tickly",
    })

    unmount()
    creation.resolve(makeTask("created", 1))
    await expect(request).resolves.toBeUndefined()
    expect(tasks.listTasks).toHaveBeenCalledOnce()
    expect(tasks.listTaskTopics).toHaveBeenCalledOnce()
  })

  it("保存写请求在卸载后成功不会启动列表刷新", async () => {
    const root = makeTask("root", 1)
    const saving = deferred<Task>()
    tasks.listTasks.mockResolvedValueOnce({
      items: [makeGroup(root)],
      next_cursor: null,
    })
    tasks.updateTask.mockReturnValueOnce(saving.promise)
    const { result, unmount } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    const request = result.current.actions.save(root.id, { title: "新标题" })

    unmount()
    saving.resolve({ ...root, title: "新标题" })
    await expect(request).resolves.toBeUndefined()
    expect(tasks.listTasks).toHaveBeenCalledOnce()
  })

  it("状态写请求在卸载后失败仍向调用方抛错且不启动刷新", async () => {
    const root = makeTask("root", 1)
    const statusUpdate = deferred<Task>()
    tasks.listTasks.mockResolvedValueOnce({
      items: [makeGroup(root)],
      next_cursor: null,
    })
    tasks.updateTask.mockReturnValueOnce(statusUpdate.promise)
    const { result, unmount } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    const request = result.current.actions.changeStatus(root, "completed")

    unmount()
    statusUpdate.reject(new Error("卸载后状态失败"))
    await expect(request).rejects.toThrow("卸载后状态失败")
    expect(tasks.listTasks).toHaveBeenCalledOnce()
  })

  it("删除写请求在卸载后成功不会启动列表或主题刷新", async () => {
    const root = makeTask("root", 1)
    const deletion = deferred<void>()
    tasks.listTasks.mockResolvedValueOnce({
      items: [makeGroup(root)],
      next_cursor: null,
    })
    tasks.deleteTask.mockReturnValueOnce(deletion.promise)
    const { result, unmount } = renderHook(() => useTaskWorkspace())
    await waitFor(() => expect(result.current.state.initialLoading).toBe(false))
    const request = result.current.actions.remove(root.id)

    unmount()
    deletion.resolve()
    await expect(request).resolves.toBeUndefined()
    expect(tasks.listTasks).toHaveBeenCalledOnce()
    expect(tasks.listTaskTopics).toHaveBeenCalledOnce()
  })
})
