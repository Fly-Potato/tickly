import { apiFetch } from "@/features/auth/auth-api"
import { responseError } from "@/lib/api-error"

export type TaskPriority = "low" | "medium" | "high"
export type TaskStatus = "new" | "in_progress" | "completed"
export type TaskStatusFilter = "all" | TaskStatus
export type TaskSort = "serial" | "created_at" | "due_at" | "priority"
export type SortOrder = "asc" | "desc"

export type Task = {
  id: string
  serial: number
  title: string
  description: string
  priority: TaskPriority | null
  topic: string
  status: TaskStatus
  due_at: string | null
  completed_at: string | null
  parent_id: string | null
  created_at: string
  updated_at: string
}

export type TaskGroup = {
  task: Task
  children: Task[]
  child_count: number
  completed_child_count: number
  context_only: boolean
}

export type TaskPage = {
  items: TaskGroup[]
  next_cursor: string | null
}

export type TaskListQuery = {
  status: TaskStatusFilter
  topic?: string
  sort: TaskSort
  order: SortOrder
  limit: number
  cursor?: string
}

export type TaskCreateInput = {
  title: string
  description?: string | null
  priority?: TaskPriority | null
  topic: string
  due_at?: string | null
  parent_id?: string | null
}

export type TaskUpdateInput = Partial<
  Pick<
    Task,
    | "title"
    | "description"
    | "priority"
    | "topic"
    | "status"
    | "due_at"
    | "parent_id"
  >
>

export type ParentTaskOption = Pick<
  Task,
  "id" | "serial" | "title" | "topic" | "status"
>

export type ParentOptionQuery = {
  query?: string
  cursor?: string
  limit: number
}

export type ParentOptionPage = {
  items: ParentTaskOption[]
  next_cursor: string | null
}

export const DEFAULT_TASK_QUERY = {
  status: "all",
  sort: "created_at",
  order: "desc",
  limit: 50,
} as const satisfies TaskListQuery

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw await responseError(response)
  }
  return (await response.json()) as T
}

export async function listTasks(
  query: TaskListQuery,
  signal?: AbortSignal,
): Promise<TaskPage> {
  const params = new URLSearchParams({
    status: query.status,
    sort: query.sort,
    order: query.order,
    limit: String(query.limit),
  })
  if (query.topic !== undefined) {
    params.set("topic", query.topic)
  }
  if (query.cursor !== undefined) {
    params.set("cursor", query.cursor)
  }
  const response = await apiFetch(`/api/v1/tasks?${params.toString()}`, {
    signal,
  })
  return readJson<TaskPage>(response)
}

export async function listTaskTopics(): Promise<string[]> {
  const response = await apiFetch("/api/v1/tasks/topics")
  const body = await readJson<{ items: string[] }>(response)
  return body.items
}

export async function listParentOptions(
  query: ParentOptionQuery,
  signal?: AbortSignal,
): Promise<ParentOptionPage> {
  const params = new URLSearchParams({ limit: String(query.limit) })
  if (query.query !== undefined) {
    params.set("query", query.query)
  }
  if (query.cursor !== undefined) {
    params.set("cursor", query.cursor)
  }
  const response = await apiFetch(
    `/api/v1/tasks/parent-options?${params.toString()}`,
    { signal },
  )
  return readJson<ParentOptionPage>(response)
}

function createTaskPayload(input: TaskCreateInput): TaskCreateInput {
  // TypeScript 结构类型不会移除运行时额外字段，API 边界必须显式挑选可写字段。
  const payload: TaskCreateInput = {
    title: input.title,
    topic: input.topic,
  }
  if (input.description !== undefined) {
    payload.description = input.description
  }
  if (input.priority !== undefined) {
    payload.priority = input.priority
  }
  if (input.due_at !== undefined) {
    payload.due_at = input.due_at
  }
  if (input.parent_id !== undefined) {
    payload.parent_id = input.parent_id
  }
  return payload
}

function updateTaskPayload(input: TaskUpdateInput): TaskUpdateInput {
  const payload: TaskUpdateInput = {}
  if (input.title !== undefined) {
    payload.title = input.title
  }
  if (input.description !== undefined) {
    payload.description = input.description
  }
  if (input.priority !== undefined) {
    payload.priority = input.priority
  }
  if (input.topic !== undefined) {
    payload.topic = input.topic
  }
  if (input.status !== undefined) {
    payload.status = input.status
  }
  if (input.due_at !== undefined) {
    payload.due_at = input.due_at
  }
  if (input.parent_id !== undefined) {
    payload.parent_id = input.parent_id
  }
  return payload
}

export async function createTask(input: TaskCreateInput): Promise<Task> {
  const response = await apiFetch("/api/v1/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(createTaskPayload(input)),
  })
  return readJson<Task>(response)
}

export async function updateTask(
  taskId: string,
  input: TaskUpdateInput,
): Promise<Task> {
  const payload = updateTaskPayload(input)
  if (Object.keys(payload).length === 0) {
    throw new Error("至少需要提供一个可更新字段")
  }
  const response = await apiFetch(
    `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  )
  return readJson<Task>(response)
}

export async function deleteTask(taskId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    { method: "DELETE" },
  )
  if (!response.ok) {
    throw await responseError(response)
  }
}
