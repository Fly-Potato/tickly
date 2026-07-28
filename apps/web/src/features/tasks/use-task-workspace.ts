import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { safeErrorMessage } from "@/lib/api-error"
import {
  DEFAULT_TASK_QUERY,
  listTasks,
  type SortOrder,
  type Task,
  type TaskListQuery,
  type TaskSort,
  type TaskStatus,
} from "./task-api"

type WorkspaceQuery = Omit<TaskListQuery, "cursor">

export type TaskWorkspaceState = {
  query: WorkspaceQuery
  items: Task[]
  nextCursor: string | null
  initialLoading: boolean
  loadingMore: boolean
  error: string | null
  selectedTaskId: string | null
}

export type TaskWorkspaceActions = {
  setStatus(status: TaskStatus): void
  setSort(sort: TaskSort): void
  setOrder(order: SortOrder): void
  retry(): Promise<void>
  loadMore(): Promise<void>
  selectTask(taskId: string | null): void
}

const initialQuery: WorkspaceQuery = { ...DEFAULT_TASK_QUERY }

function appendUniqueTasks(current: Task[], incoming: Task[]): Task[] {
  const known = new Set(current.map((task) => task.id))
  return current.concat(incoming.filter((task) => !known.has(task.id)))
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
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
  })
  const stateRef = useRef(state)
  const queryRef = useRef(initialQuery)
  const nextCursorRef = useRef<string | null>(null)
  const generationRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const loadingMoreRef = useRef(false)

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

  const actions = useMemo<TaskWorkspaceActions>(
    () => ({
      setStatus: (status) => updateQuery({ status }),
      setSort: (sort) => updateQuery({ sort }),
      setOrder: (order) => updateQuery({ order }),
      retry,
      loadMore,
      selectTask: (selectedTaskId) =>
        setState((current) => ({ ...current, selectedTaskId })),
    }),
    [loadMore, retry, updateQuery],
  )

  return { state, actions }
}
