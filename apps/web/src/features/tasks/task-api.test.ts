import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "@/lib/api-error"

const api = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock("@/features/auth/auth-api", () => ({
  apiFetch: api.apiFetch,
}))

import {
  createTask,
  deleteTask,
  listTasks,
  updateTask,
  type Task,
  type TaskListQuery,
} from "./task-api"

const task: Task = {
  id: "task-id",
  title: "阶段 4",
  notes: null,
  is_completed: false,
  priority: "none",
  due_at: null,
  completed_at: null,
  created_at: "2026-07-28T08:00:00Z",
  updated_at: "2026-07-28T08:00:00Z",
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

beforeEach(() => {
  api.apiFetch.mockReset()
})

describe("任务 API 客户端", () => {
  it("编码列表 query 和 cursor 并传递取消信号", async () => {
    const controller = new AbortController()
    const query: TaskListQuery = {
      status: "active",
      sort: "priority",
      order: "asc",
      limit: 50,
      cursor: "next/+==",
    }
    api.apiFetch.mockResolvedValue(
      jsonResponse({ items: [task], next_cursor: null }),
    )

    await expect(listTasks(query, controller.signal)).resolves.toEqual({
      items: [task],
      next_cursor: null,
    })
    expect(api.apiFetch).toHaveBeenCalledWith(
      "/api/v1/tasks?status=active&sort=priority&order=asc&limit=50&cursor=next%2F%2B%3D%3D",
      { signal: controller.signal },
    )
  })

  it("创建只发送给定字段且更新编码任务 ID", async () => {
    api.apiFetch.mockResolvedValue(jsonResponse(task, 201))

    await createTask({ title: "阶段 4" })
    expect(api.apiFetch).toHaveBeenLastCalledWith("/api/v1/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "阶段 4" }),
    })

    api.apiFetch.mockResolvedValue(jsonResponse(task))
    await updateTask("task/id", { notes: null, due_at: null })
    expect(api.apiFetch).toHaveBeenLastCalledWith("/api/v1/tasks/task%2Fid", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: null, due_at: null }),
    })
  })

  it("删除成功不解析 204 响应体", async () => {
    const response = new Response(null, { status: 204 })
    const jsonSpy = vi.spyOn(response, "json")
    api.apiFetch.mockResolvedValue(response)

    await expect(deleteTask("task-id")).resolves.toBeUndefined()

    expect(api.apiFetch).toHaveBeenCalledWith("/api/v1/tasks/task-id", {
      method: "DELETE",
    })
    expect(jsonSpy).not.toHaveBeenCalled()
  })

  it("非成功响应转换为安全 ApiError", async () => {
    api.apiFetch.mockResolvedValue(
      jsonResponse(
        { error: { code: "task_not_found", message: "任务不存在" } },
        404,
      ),
    )

    await expect(deleteTask("missing")).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 404,
        code: "task_not_found",
        message: "任务不存在",
      }),
    )
  })
})
