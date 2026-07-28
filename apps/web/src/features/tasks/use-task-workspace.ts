import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { safeErrorMessage } from "@/lib/api-error"
import {
  DEFAULT_TASK_QUERY,
  createTask,
  deleteTask,
  listTasks,
  updateTask,
  type SortOrder,
  type Task,
  type TaskListQuery,
  type TaskSort,
  type TaskStatus,
  type TaskUpdateInput,
} from "./task-api"

/**
 * Todo 工作区的服务端状态边界。
 *
 * 列表请求以 query generation 和 AbortController 双重隔离，旧响应不得写回新
 * 筛选。mutation 不建立浏览器缓存：创建和保存后重读服务端顺序，删除直接移除，
 * 完成切换仅在同一 generation 内回滚原快照；query 已变化时改为重读，避免把旧
 * 筛选中的任务注入新列表。未知异常只写入稳定界面提示，同时向调用组件重新抛出，
 * 让表单保留输入并在触发位置展示错误。
 */

type WorkspaceQuery = Omit<TaskListQuery, "cursor">

export type TaskWorkspaceState = {
  query: WorkspaceQuery
  items: Task[]
  nextCursor: string | null
  initialLoading: boolean
  loadingMore: boolean
  error: string | null
  selectedTaskId: string | null
  creating: boolean
  saving: boolean
  deleting: boolean
  completingTaskIds: ReadonlySet<string>
  completionError: string | null
}

export type TaskWorkspaceActions = {
  setStatus(status: TaskStatus): void
  setSort(sort: TaskSort): void
  setOrder(order: SortOrder): void
  retry(): Promise<void>
  loadMore(): Promise<void>
  selectTask(taskId: string | null): void
  create(title: string): Promise<void>
  save(taskId: string, patch: TaskUpdateInput): Promise<void>
  setCompleted(task: Task, completed: boolean): Promise<void>
  remove(taskId: string): Promise<void>
}

const initialQuery: WorkspaceQuery = { ...DEFAULT_TASK_QUERY }

function appendUniqueTasks(current: Task[], incoming: Task[]): Task[] {
  const known = new Set(current.map((task) => task.id))
  return current.concat(incoming.filter((task) => !known.has(task.id)))
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}

function matchesStatus(task: Task, status: TaskStatus): boolean {
  return (
    status === "all" ||
    (status === "active" && !task.is_completed) ||
    (status === "completed" && task.is_completed)
  )
}

export function useTaskWorkspace(): {
  state: TaskWorkspaceState
  actions: TaskWorkspaceActions
} {
  const [state, setState] = useState<TaskWorkspaceState>({
    query: initialQuery,
    items: [],
    nextCursor: null,
    initialLoading: true,
    loadingMore: false,
    error: null,
    selectedTaskId: null,
    creating: false,
    saving: false,
    deleting: false,
    completingTaskIds: new Set(),
    completionError: null,
  })
  const stateRef = useRef(state)
  const queryRef = useRef(initialQuery)
  const nextCursorRef = useRef<string | null>(null)
  const generationRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const loadingMoreRef = useRef(false)
  const creatingRef = useRef(false)
  const savingRef = useRef(false)
  const deletingRef = useRef(false)
  const completingTaskIdsRef = useRef(new Set<string>())

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const loadFirstPage = useCallback(async (query: WorkspaceQuery) => {
    // generation 防止不支持或来不及处理 abort 的旧响应覆盖新筛选结果。
    const generation = ++generationRef.current
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    nextCursorRef.current = null
    loadingMoreRef.current = false
    setState((current) => ({
      ...current,
      items: [],
      nextCursor: null,
      initialLoading: true,
      loadingMore: false,
      error: null,
    }))

    try {
      const page = await listTasks(query, controller.signal)
      if (generationRef.current !== generation) {
        return
      }
      nextCursorRef.current = page.next_cursor
      setState((current) => ({
        ...current,
        items: page.items,
        nextCursor: page.next_cursor,
        initialLoading: false,
        error: null,
        selectedTaskId:
          current.selectedTaskId === null ||
          page.items.some((task) => task.id === current.selectedTaskId)
            ? current.selectedTaskId
            : null,
      }))
    } catch (error) {
      if (generationRef.current !== generation || isAbortError(error)) {
        return
      }
      setState((current) => ({
        ...current,
        initialLoading: false,
        error: safeErrorMessage(error, "任务加载失败"),
      }))
    }
  }, [])

  useEffect(() => {
    void loadFirstPage(state.query)
    return () => controllerRef.current?.abort()
  }, [loadFirstPage, state.query])

  const updateQuery = useCallback((patch: Partial<WorkspaceQuery>) => {
    const next = { ...queryRef.current, ...patch }
    if (
      next.status === queryRef.current.status &&
      next.sort === queryRef.current.sort &&
      next.order === queryRef.current.order &&
      next.limit === queryRef.current.limit
    ) {
      return
    }
    queryRef.current = next
    setState((current) => ({ ...current, query: next }))
  }, [])

  const loadMore = useCallback(async () => {
    const cursor = nextCursorRef.current
    if (cursor === null || loadingMoreRef.current) {
      return
    }
    const generation = generationRef.current
    const controller = new AbortController()
    controllerRef.current = controller
    loadingMoreRef.current = true
    setState((current) => ({ ...current, loadingMore: true, error: null }))

    try {
      const page = await listTasks(
        { ...queryRef.current, cursor },
        controller.signal,
      )
      if (generationRef.current !== generation) {
        return
      }
      nextCursorRef.current = page.next_cursor
      setState((current) => ({
        ...current,
        items: appendUniqueTasks(current.items, page.items),
        nextCursor: page.next_cursor,
        loadingMore: false,
        error: null,
      }))
    } catch (error) {
      if (generationRef.current !== generation || isAbortError(error)) {
        return
      }
      setState((current) => ({
        ...current,
        loadingMore: false,
        error: safeErrorMessage(error, "任务加载失败"),
      }))
    } finally {
      if (generationRef.current === generation) {
        loadingMoreRef.current = false
      }
    }
  }, [])

  const retry = useCallback(async () => {
    if (stateRef.current.items.length > 0 && nextCursorRef.current !== null) {
      await loadMore()
      return
    }
    await loadFirstPage(queryRef.current)
  }, [loadFirstPage, loadMore])

  const create = useCallback(
    async (title: string) => {
      const normalizedTitle = title.trim()
      if (normalizedTitle === "" || creatingRef.current) {
        return
      }
      creatingRef.current = true
      setState((current) => ({ ...current, creating: true }))
      try {
        await createTask({ title: normalizedTitle })
        await loadFirstPage(queryRef.current)
      } finally {
        creatingRef.current = false
        setState((current) => ({ ...current, creating: false }))
      }
    },
    [loadFirstPage],
  )

  const save = useCallback(
    async (taskId: string, patch: TaskUpdateInput) => {
      if (savingRef.current) {
        return
      }
      savingRef.current = true
      setState((current) => ({ ...current, saving: true }))
      try {
        await updateTask(taskId, patch)
        setState((current) => ({ ...current, selectedTaskId: null }))
        await loadFirstPage(queryRef.current)
      } finally {
        savingRef.current = false
        setState((current) => ({ ...current, saving: false }))
      }
    },
    [loadFirstPage],
  )

  const remove = useCallback(async (taskId: string) => {
    if (deletingRef.current) {
      return
    }
    deletingRef.current = true
    setState((current) => ({ ...current, deleting: true }))
    try {
      await deleteTask(taskId)
      setState((current) => ({
        ...current,
        items: current.items.filter((task) => task.id !== taskId),
        selectedTaskId: null,
      }))
    } finally {
      deletingRef.current = false
      setState((current) => ({ ...current, deleting: false }))
    }
  }, [])

  const setCompleted = useCallback(
    async (task: Task, completed: boolean) => {
      if (completingTaskIdsRef.current.has(task.id)) {
        return
      }
      const originalIndex = stateRef.current.items.findIndex(
        (item) => item.id === task.id,
      )
      if (originalIndex < 0) {
        return
      }
      const originalTask = stateRef.current.items[originalIndex]
      const originalGeneration = generationRef.current
      const originalQuery = queryRef.current
      const optimisticTask = {
        ...originalTask,
        is_completed: completed,
        completed_at: completed ? originalTask.completed_at : null,
      }

      completingTaskIdsRef.current.add(task.id)
      setState((current) => ({
        ...current,
        items: matchesStatus(optimisticTask, current.query.status)
          ? current.items.map((item) =>
              item.id === task.id ? optimisticTask : item,
            )
          : current.items.filter((item) => item.id !== task.id),
        completingTaskIds: new Set(completingTaskIdsRef.current),
        completionError: null,
      }))

      try {
        const updated = await updateTask(task.id, { is_completed: completed })
        if (
          generationRef.current === originalGeneration &&
          queryRef.current === originalQuery
        ) {
          setState((current) => ({
            ...current,
            items: current.items.map((item) =>
              item.id === task.id ? updated : item,
            ),
          }))
        }
      } catch (error) {
        if (
          generationRef.current === originalGeneration &&
          queryRef.current === originalQuery
        ) {
          setState((current) => {
            const restored = current.items.filter(
              (item) => item.id !== originalTask.id,
            )
            restored.splice(
              Math.min(originalIndex, restored.length),
              0,
              originalTask,
            )
            return {
              ...current,
              items: restored,
              completionError: "完成状态更新失败",
            }
          })
        } else {
          setState((current) => ({
            ...current,
            completionError: "完成状态更新失败",
          }))
          await loadFirstPage(queryRef.current)
        }
        throw error
      } finally {
        completingTaskIdsRef.current.delete(task.id)
        setState((current) => ({
          ...current,
          completingTaskIds: new Set(completingTaskIdsRef.current),
        }))
      }
    },
    [loadFirstPage],
  )

  const actions = useMemo<TaskWorkspaceActions>(
    () => ({
      setStatus: (status) => updateQuery({ status }),
      setSort: (sort) => updateQuery({ sort }),
      setOrder: (order) => updateQuery({ order }),
      retry,
      loadMore,
      create,
      save,
      setCompleted,
      remove,
      selectTask: (selectedTaskId) =>
        setState((current) => ({ ...current, selectedTaskId })),
    }),
    [create, loadMore, remove, retry, save, setCompleted, updateQuery],
  )

  return { state, actions }
}
