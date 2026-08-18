import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { safeErrorMessage } from "@/lib/api-error"
import {
  DEFAULT_TASK_QUERY,
  createTask,
  deleteTask,
  listTaskTopics,
  listTasks,
  updateTask,
  type SortOrder,
  type Task,
  type TaskCreateInput,
  type TaskGroup,
  type TaskListQuery,
  type TaskSort,
  type TaskStatus,
  type TaskStatusFilter,
  type TaskUpdateInput,
} from "./task-api"

/**
 * Todo 工作区的服务端状态边界。
 *
 * 列表使用 AbortController 与请求代际共同隔离筛选竞态；主题请求拥有独立错误
 * 域。任务树只按稳定 ID 定位节点，mutation 的服务端响应先替换对应节点，再重读
 * 当前 query，让筛选、上下文根节点和子任务计数始终以服务端结果为准。
 */

export type WorkspaceQuery = Omit<TaskListQuery, "cursor">

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

const initialQuery: WorkspaceQuery = { ...DEFAULT_TASK_QUERY }
const STATUS_MUTATION_CONFLICT_MESSAGE = "任务状态正在更新，请稍后重试"
const STRUCTURAL_MUTATION_CONFLICT_MESSAGE = "已有任务操作正在进行中"

function appendUniqueGroups(
  current: TaskGroup[],
  incoming: TaskGroup[]
): TaskGroup[] {
  const knownRootIds = new Set(current.map((group) => group.task.id))
  const next = current.slice()
  for (const group of incoming) {
    if (knownRootIds.has(group.task.id)) {
      continue
    }
    knownRootIds.add(group.task.id)
    next.push(group)
  }
  return next
}

function taskMatchesQuery(task: Task, query: WorkspaceQuery): boolean {
  return (
    (query.status === "all" || task.status === query.status) &&
    (query.topic === undefined || task.topic === query.topic)
  )
}

function appendCreatedChildToParent(
  groups: TaskGroup[],
  child: Task,
  query: WorkspaceQuery
): TaskGroup[] {
  if (child.parent_id === null) return groups
  const groupIndex = groups.findIndex(
    (group) => group.task.id === child.parent_id
  )
  if (groupIndex < 0) return groups

  const group = groups[groupIndex]
  if (group.children.some((current) => current.id === child.id)) return groups
  // 根命中筛选时展示完整子列表；仅上下文根则只展示自身命中的子任务。
  const shouldDisplayChild =
    taskMatchesQuery(group.task, query) || taskMatchesQuery(child, query)
  const nextGroups = groups.slice()
  nextGroups[groupIndex] = {
    ...group,
    children: shouldDisplayChild ? [...group.children, child] : group.children,
    child_count: group.child_count + 1,
    completed_child_count:
      group.completed_child_count + (child.status === "completed" ? 1 : 0),
  }
  return nextGroups
}

function preserveCreatedChildParent(
  pageItems: TaskGroup[],
  currentItems: TaskGroup[],
  preserveRootId?: string
): TaskGroup[] {
  if (
    preserveRootId === undefined ||
    pageItems.some((group) => group.task.id === preserveRootId)
  ) {
    return pageItems
  }
  const preserved = currentItems.find(
    (group) => group.task.id === preserveRootId
  )
  return preserved === undefined ? pageItems : [...pageItems, preserved]
}

export function findTaskInGroups(
  groups: TaskGroup[],
  taskId: string
): Task | null {
  for (const group of groups) {
    if (group.task.id === taskId) {
      return group.task
    }
    for (const child of group.children) {
      if (child.id === taskId) {
        return child
      }
    }
  }
  return null
}

function updateTaskInGroups(
  groups: TaskGroup[],
  taskId: string,
  update: (current: Task) => Task
): TaskGroup[] {
  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const group = groups[groupIndex]
    if (group.task.id === taskId) {
      const nextTask = update(group.task)
      if (nextTask === group.task) {
        return groups
      }
      const nextGroups = groups.slice()
      nextGroups[groupIndex] = { ...group, task: nextTask }
      return nextGroups
    }

    const childIndex = group.children.findIndex((child) => child.id === taskId)
    if (childIndex >= 0) {
      const nextChild = update(group.children[childIndex])
      if (nextChild === group.children[childIndex]) {
        return groups
      }
      const nextChildren = group.children.slice()
      nextChildren[childIndex] = nextChild
      const nextGroups = groups.slice()
      nextGroups[groupIndex] = {
        ...group,
        children: nextChildren,
        completed_child_count: nextChildren.reduce(
          (count, child) => count + (child.status === "completed" ? 1 : 0),
          0
        ),
      }
      return nextGroups
    }
  }
  return groups
}

function replaceTaskInGroups(groups: TaskGroup[], task: Task): TaskGroup[] {
  return updateTaskInGroups(groups, task.id, () => task)
}

function restoreTaskStatusInGroups(
  groups: TaskGroup[],
  task: Task,
  optimisticStatus: TaskStatus
): TaskGroup[] {
  return updateTaskInGroups(groups, task.id, (current) => {
    // 只回滚本次 mutation 拥有的字段，保留并发保存返回的标题、主题等新值。
    if (current.status !== optimisticStatus) {
      return current
    }
    return {
      ...current,
      status: task.status,
      completed_at: task.completed_at,
    }
  })
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError"
}

function queriesEqual(left: WorkspaceQuery, right: WorkspaceQuery): boolean {
  return (
    left.status === right.status &&
    left.topic === right.topic &&
    left.sort === right.sort &&
    left.order === right.order &&
    left.limit === right.limit
  )
}

function createInitialState(): TaskWorkspaceState {
  return {
    query: { ...initialQuery },
    items: [],
    topics: [],
    topicLoading: true,
    topicError: null,
    nextCursor: null,
    initialLoading: true,
    loadingMore: false,
    error: null,
    selectedTaskId: null,
    creating: false,
    saving: false,
    deleting: false,
    statusMutatingTaskIds: new Set(),
    statusError: null,
  }
}

export function useTaskWorkspace(): {
  state: TaskWorkspaceState
  actions: TaskWorkspaceActions
} {
  const [state, setState] = useState(createInitialState)
  const queryRef = useRef<WorkspaceQuery>({ ...initialQuery })
  const nextCursorRef = useRef<string | null>(null)
  const requestGenerationRef = useRef(0)
  const queryVersionRef = useRef(0)
  const topicsGenerationRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(false)
  const loadingMoreRef = useRef(false)
  const structuralMutationOwnerRef = useRef<symbol | null>(null)
  const statusMutatingTaskIdsRef = useRef<ReadonlySet<string>>(new Set())

  const loadFirstPage = useCallback(
    async (
      query: WorkspaceQuery,
      retainItems = false,
      preserveRootId?: string
    ) => {
      if (!mountedRef.current) {
        return
      }
      // abort 负责尽快释放旧请求，generation 负责隔离忽略 signal 的请求实现。
      const generation = ++requestGenerationRef.current
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller
      nextCursorRef.current = null
      loadingMoreRef.current = false
      setState((current) => ({
        ...current,
        items: retainItems ? current.items : [],
        nextCursor: null,
        initialLoading: true,
        loadingMore: false,
        error: null,
      }))

      try {
        const page = await listTasks(query, controller.signal)
        if (
          !mountedRef.current ||
          requestGenerationRef.current !== generation
        ) {
          return
        }
        nextCursorRef.current = page.next_cursor
        setState((current) => {
          // 只保留刚创建子任务时仍打开的分页父 Group；这不是跨查询通用缓存。
          const items = preserveCreatedChildParent(
            page.items,
            current.items,
            preserveRootId
          )
          return {
            ...current,
            items,
            nextCursor: page.next_cursor,
            initialLoading: false,
            error: null,
            selectedTaskId:
              current.selectedTaskId !== null &&
              findTaskInGroups(items, current.selectedTaskId) === null
                ? null
                : current.selectedTaskId,
          }
        })
      } catch (error) {
        if (
          !mountedRef.current ||
          requestGenerationRef.current !== generation ||
          isAbortError(error)
        ) {
          return
        }
        setState((current) => ({
          ...current,
          initialLoading: false,
          error: safeErrorMessage(error, "任务加载失败"),
        }))
      }
    },
    []
  )

  const loadTopics = useCallback(async () => {
    if (!mountedRef.current) {
      return
    }
    const generation = ++topicsGenerationRef.current
    setState((current) => ({
      ...current,
      topicLoading: true,
      topicError: null,
    }))
    try {
      const topics = await listTaskTopics()
      if (!mountedRef.current || topicsGenerationRef.current !== generation) {
        return
      }
      setState((current) => ({
        ...current,
        topics,
        topicLoading: false,
        topicError: null,
      }))
    } catch (error) {
      if (!mountedRef.current || topicsGenerationRef.current !== generation) {
        return
      }
      setState((current) => ({
        ...current,
        topicLoading: false,
        topicError: safeErrorMessage(error, "主题加载失败"),
      }))
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    // 两个独立请求在同一 effect 中立即启动，避免首屏 waterfall。
    void loadFirstPage(queryRef.current)
    void loadTopics()
    return () => {
      mountedRef.current = false
      requestGenerationRef.current += 1
      topicsGenerationRef.current += 1
      controllerRef.current?.abort()
      structuralMutationOwnerRef.current = null
    }
  }, [loadFirstPage, loadTopics])

  const replaceQuery = useCallback(
    (nextQuery: WorkspaceQuery) => {
      if (queriesEqual(queryRef.current, nextQuery)) {
        return
      }
      const next = { ...nextQuery }
      queryRef.current = next
      queryVersionRef.current += 1
      setState((current) => ({ ...current, query: next }))
      void loadFirstPage(next)
    },
    [loadFirstPage]
  )

  const updateQuery = useCallback(
    (patch: Partial<WorkspaceQuery>) => {
      replaceQuery({ ...queryRef.current, ...patch })
    },
    [replaceQuery]
  )

  const acquireStatusMutation = useCallback((taskId: string): boolean => {
    // ref 是同步互斥权威，不能只依赖异步提交的 React state 判断所有权。
    if (statusMutatingTaskIdsRef.current.has(taskId)) {
      return false
    }
    const pendingIds = new Set(statusMutatingTaskIdsRef.current)
    pendingIds.add(taskId)
    statusMutatingTaskIdsRef.current = pendingIds
    if (mountedRef.current) {
      setState((current) => ({
        ...current,
        statusMutatingTaskIds: pendingIds,
        statusError: null,
      }))
    }
    return true
  }, [])

  const releaseStatusMutation = useCallback((taskId: string) => {
    if (!statusMutatingTaskIdsRef.current.has(taskId)) {
      return
    }
    const remainingIds = new Set(statusMutatingTaskIdsRef.current)
    remainingIds.delete(taskId)
    statusMutatingTaskIdsRef.current = remainingIds
    if (mountedRef.current) {
      setState((current) => ({
        ...current,
        statusMutatingTaskIds: remainingIds,
      }))
    }
  }, [])

  const acquireStructuralMutation = useCallback((): symbol => {
    // React state 的提交晚于事件调用；同步 owner 才能阻止同一 render 的跨操作写入。
    if (structuralMutationOwnerRef.current !== null) {
      throw new Error(STRUCTURAL_MUTATION_CONFLICT_MESSAGE)
    }
    const owner = Symbol("task-structural-mutation")
    structuralMutationOwnerRef.current = owner
    return owner
  }, [])

  const releaseStructuralMutation = useCallback((owner: symbol) => {
    // 卸载会先清空 owner；旧请求完成时不能释放后续调用可能持有的新 owner。
    if (structuralMutationOwnerRef.current === owner) {
      structuralMutationOwnerRef.current = null
    }
  }, [])

  const loadMore = useCallback(async () => {
    const cursor = nextCursorRef.current
    if (cursor === null || loadingMoreRef.current) {
      return
    }

    const generation = requestGenerationRef.current
    const query = { ...queryRef.current, cursor }
    const controller = new AbortController()
    controllerRef.current = controller
    loadingMoreRef.current = true
    setState((current) => ({ ...current, loadingMore: true, error: null }))

    try {
      const page = await listTasks(query, controller.signal)
      if (!mountedRef.current || requestGenerationRef.current !== generation) {
        return
      }
      nextCursorRef.current = page.next_cursor
      setState((current) => ({
        ...current,
        items: appendUniqueGroups(current.items, page.items),
        nextCursor: page.next_cursor,
        loadingMore: false,
        error: null,
      }))
    } catch (error) {
      if (
        !mountedRef.current ||
        requestGenerationRef.current !== generation ||
        isAbortError(error)
      ) {
        return
      }
      setState((current) => ({
        ...current,
        loadingMore: false,
        error: safeErrorMessage(error, "任务加载失败"),
      }))
    } finally {
      if (requestGenerationRef.current === generation) {
        loadingMoreRef.current = false
      }
    }
  }, [])

  const retry = useCallback(async () => {
    if (nextCursorRef.current !== null) {
      await loadMore()
      return
    }
    await loadFirstPage(queryRef.current)
  }, [loadFirstPage, loadMore])

  const create = useCallback(
    async (input: TaskCreateInput) => {
      const structuralOwner = acquireStructuralMutation()
      try {
        setState((current) => ({ ...current, creating: true }))
        const created = await createTask(input)
        if (!mountedRef.current) {
          return
        }
        const currentQuery = queryRef.current
        if (created.parent_id !== null) {
          setState((current) => ({
            ...current,
            items: appendCreatedChildToParent(
              current.items,
              created,
              currentQuery
            ),
          }))
        }
        await Promise.all([
          loadFirstPage(
            currentQuery,
            true,
            created.parent_id === null ? undefined : created.parent_id
          ),
          loadTopics(),
        ])
      } finally {
        releaseStructuralMutation(structuralOwner)
        if (mountedRef.current) {
          setState((current) => ({ ...current, creating: false }))
        }
      }
    },
    [
      acquireStructuralMutation,
      loadFirstPage,
      loadTopics,
      releaseStructuralMutation,
    ]
  )

  const save = useCallback(
    async (taskId: string, patch: TaskUpdateInput) => {
      const updatesStatus = patch.status !== undefined
      const structuralOwner = acquireStructuralMutation()
      let ownsStatusMutation = false
      try {
        ownsStatusMutation = updatesStatus
          ? acquireStatusMutation(taskId)
          : false
        if (updatesStatus && !ownsStatusMutation) {
          throw new Error(STATUS_MUTATION_CONFLICT_MESSAGE)
        }
        setState((current) => ({ ...current, saving: true }))
        const updated = await updateTask(taskId, patch)
        if (!mountedRef.current) {
          return
        }
        setState((current) => ({
          ...current,
          items: replaceTaskInGroups(current.items, updated),
        }))
        await Promise.all([
          loadFirstPage(queryRef.current, true),
          ...(patch.topic === undefined ? [] : [loadTopics()]),
        ])
      } finally {
        if (ownsStatusMutation) {
          releaseStatusMutation(taskId)
        }
        releaseStructuralMutation(structuralOwner)
        if (mountedRef.current) {
          setState((current) => ({ ...current, saving: false }))
        }
      }
    },
    [
      acquireStatusMutation,
      acquireStructuralMutation,
      loadFirstPage,
      loadTopics,
      releaseStatusMutation,
      releaseStructuralMutation,
    ]
  )

  const changeStatus = useCallback(
    async (task: Task, status: TaskStatus) => {
      if (!acquireStatusMutation(task.id)) {
        return
      }

      const originalQueryVersion = queryVersionRef.current
      const optimisticTask: Task = {
        ...task,
        status,
        completed_at: status === "completed" ? task.completed_at : null,
      }
      setState((current) => ({
        ...current,
        items: replaceTaskInGroups(current.items, optimisticTask),
      }))

      try {
        const updated = await updateTask(task.id, { status })
        if (!mountedRef.current) {
          return
        }
        setState((current) => ({
          ...current,
          items: replaceTaskInGroups(current.items, updated),
        }))
        await loadFirstPage(queryRef.current, true)
      } catch (error) {
        if (mountedRef.current) {
          setState((current) => ({
            ...current,
            // query 改变后不把旧筛选中的快照注入新列表。
            items:
              queryVersionRef.current === originalQueryVersion
                ? restoreTaskStatusInGroups(current.items, task, status)
                : current.items,
            statusError: "任务状态更新失败",
          }))
        }
        throw error
      } finally {
        releaseStatusMutation(task.id)
      }
    },
    [acquireStatusMutation, loadFirstPage, releaseStatusMutation]
  )

  const remove = useCallback(
    async (taskId: string) => {
      const structuralOwner = acquireStructuralMutation()
      try {
        setState((current) => ({ ...current, deleting: true }))
        await deleteTask(taskId)
        if (!mountedRef.current) {
          return
        }
        setState((current) => ({ ...current, selectedTaskId: null }))
        // 删除父任务会提升子任务，必须由服务端重建树和主题集合。
        await Promise.all([loadFirstPage(queryRef.current, true), loadTopics()])
      } finally {
        releaseStructuralMutation(structuralOwner)
        if (mountedRef.current) {
          setState((current) => ({ ...current, deleting: false }))
        }
      }
    },
    [
      acquireStructuralMutation,
      loadFirstPage,
      loadTopics,
      releaseStructuralMutation,
    ]
  )

  const actions = useMemo<TaskWorkspaceActions>(
    () => ({
      setStatus: (status) => updateQuery({ status }),
      setTopic: (topic) => updateQuery({ topic }),
      setSort: (sort) => updateQuery({ sort }),
      setOrder: (order) => updateQuery({ order }),
      applyQuery: replaceQuery,
      retry,
      retryTopics: loadTopics,
      loadMore,
      create,
      save,
      changeStatus,
      remove,
      selectTask: (selectedTaskId) =>
        setState((current) => ({ ...current, selectedTaskId })),
    }),
    [
      changeStatus,
      create,
      loadMore,
      loadTopics,
      remove,
      replaceQuery,
      retry,
      save,
      updateQuery,
    ]
  )

  return { state, actions }
}
