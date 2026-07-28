import { apiFetch } from "@/features/auth/auth-api"
import { responseError } from "@/lib/api-error"

export type TaskPriority = "none" | "low" | "medium" | "high"
export type TaskStatus = "all" | "active" | "completed"
export type TaskSort = "created_at" | "due_at" | "priority"
export type SortOrder = "asc" | "desc"

export type Task = {
  id: string
  title: string
  notes: string | null
  is_completed: boolean
  priority: TaskPriority
  due_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export type TaskPage = {
  items: Task[]
  next_cursor: string | null
}

export type TaskListQuery = {
  status: TaskStatus
  sort: TaskSort
  order: SortOrder
  limit: number
  cursor?: string
}

export type TaskCreateInput = {
  title: string
  notes?: string | null
  priority?: TaskPriority
  due_at?: string | null
}

export type TaskUpdateInput = Partial<
  Pick<Task, "title" | "notes" | "priority" | "due_at" | "is_completed">
>

export const DEFAULT_TASK_QUERY = {
  status: "all",
  sort: "created_at",
  order: "desc",
  limit: 50,
} satisfies TaskListQuery

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
  if (query.cursor !== undefined) {
    params.set("cursor", query.cursor)
  }
  const response = await apiFetch(`/api/v1/tasks?${params.toString()}`, {
    signal,
  })
  return readJson<TaskPage>(response)
}

export async function createTask(input: TaskCreateInput): Promise<Task> {
  const response = await apiFetch("/api/v1/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  return readJson<Task>(response)
}

export async function updateTask(
  taskId: string,
  input: TaskUpdateInput,
): Promise<Task> {
  const response = await apiFetch(
    `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
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
