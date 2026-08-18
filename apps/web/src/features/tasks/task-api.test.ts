import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock("@/features/auth/auth-api", () => ({
  apiFetch: api.apiFetch,
}))

import {
  DEFAULT_TASK_QUERY,
  createTask,
  deleteTask,
  listParentOptions,
  listTaskTopics,
  listTasks,
  updateTask,
  type Task,
  type TaskGroup,
  type TaskListQuery,
} from "./task-api"

const task: Task = {
  id: "task-id",
  serial: 18,
  title: "阶段 4",
  description: "阶段 4",
  priority: null,
  topic: "Tickly",
  status: "new",
  due_at: null,
  completed_at: null,
  parent_id: null,
  created_at: "2026-07-28T08:00:00Z",
  updated_at: "2026-07-28T08:00:00Z",
}

const group: TaskGroup = {
  task,
  children: [],
  child_count: 0,
  completed_child_count: 0,
  context_only: false,
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
  it("默认 query 使用服务端约定的分页和排序", () => {
    expect(DEFAULT_TASK_QUERY).toEqual({
      status: "all",
      sort: "created_at",
      order: "desc",
      limit: 50,
    })
  })

  it("编码列表 query、主题和 cursor 并传递取消信号", async () => {
    const controller = new AbortController()
    const query: TaskListQuery = {
      status: "in_progress",
      topic: "Tickly & 工作",
      sort: "serial",
      order: "asc",
      limit: 50,
      cursor: "next/+==",
    }
    api.apiFetch.mockResolvedValue(
      jsonResponse({ items: [group], next_cursor: null }),
    )

    await expect(listTasks(query, controller.signal)).resolves.toEqual({
      items: [group],
      next_cursor: null,
    })
    expect(api.apiFetch).toHaveBeenCalledWith(
      "/api/v1/tasks?status=in_progress&sort=serial&order=asc&limit=50&topic=Tickly+%26+%E5%B7%A5%E4%BD%9C&cursor=next%2F%2B%3D%3D",
      { signal: controller.signal },
    )
  })

  it("显式空主题也通过 URLSearchParams 发送", async () => {
    api.apiFetch.mockResolvedValue(
      jsonResponse({ items: [], next_cursor: null }),
    )

    await listTasks({
      ...DEFAULT_TASK_QUERY,
      topic: "",
    })

    expect(api.apiFetch).toHaveBeenCalledWith(
      "/api/v1/tasks?status=all&sort=created_at&order=desc&limit=50&topic=",
      { signal: undefined },
    )
  })

  it("读取主题和分页父待办候选", async () => {
    const controller = new AbortController()
    api.apiFetch
      .mockResolvedValueOnce(jsonResponse({ items: ["Tickly", "工作"] }))
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              id: "parent-id",
              serial: 7,
              title: "父任务",
              topic: "Tickly",
              status: "in_progress",
            },
          ],
          next_cursor: "parent-next",
        }),
      )

    await expect(listTaskTopics()).resolves.toEqual(["Tickly", "工作"])
    await expect(
      listParentOptions({ query: "#7", limit: 20 }, controller.signal),
    ).resolves.toEqual(
      expect.objectContaining({ next_cursor: "parent-next" }),
    )

    expect(api.apiFetch).toHaveBeenNthCalledWith(1, "/api/v1/tasks/topics")
    expect(api.apiFetch).toHaveBeenNthCalledWith(
      2,
      "/api/v1/tasks/parent-options?limit=20&query=%237",
      { signal: controller.signal },
    )
  })

  it("主题和父待办候选错误都转换为安全 ApiError", async () => {
    api.apiFetch.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "topics_unavailable", message: "主题读取失败" } },
        503,
      ),
    )
    await expect(listTaskTopics()).rejects.toEqual(
      expect.objectContaining({
        status: 503,
        code: "topics_unavailable",
        message: "主题读取失败",
      }),
    )

    api.apiFetch.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "parent_options_unavailable", message: "候选读取失败" } },
        500,
      ),
    )
    await expect(
      listParentOptions({ limit: 20 }, new AbortController().signal),
    ).rejects.toEqual(
      expect.objectContaining({
        status: 500,
        code: "parent_options_unavailable",
        message: "候选读取失败",
      }),
    )
  })

  it("创建和更新只发送公开字段并编码任务 ID", async () => {
    api.apiFetch.mockResolvedValue(jsonResponse(task, 201))

    await createTask({ title: "阶段 4", topic: "Tickly" })
    expect(api.apiFetch).toHaveBeenLastCalledWith("/api/v1/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "阶段 4", topic: "Tickly" }),
    })

    api.apiFetch.mockResolvedValue(jsonResponse(task))
    await updateTask("task/id", {
      description: "详细说明",
      priority: null,
      status: "in_progress",
      due_at: null,
    })
    expect(api.apiFetch).toHaveBeenLastCalledWith("/api/v1/tasks/task%2Fid", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        description: "详细说明",
        priority: null,
        status: "in_progress",
        due_at: null,
      }),
    })
  })

  it("完整任务变量写入时只发送各接口允许的字段", async () => {
    api.apiFetch.mockResolvedValue(jsonResponse(task, 201))

    await createTask(task)
    expect(JSON.parse(api.apiFetch.mock.calls[0][1].body as string)).toEqual({
      title: "阶段 4",
      description: "阶段 4",
      priority: null,
      topic: "Tickly",
      due_at: null,
      parent_id: null,
    })

    api.apiFetch.mockResolvedValue(jsonResponse(task))
    await updateTask("task-id", task)
    expect(JSON.parse(api.apiFetch.mock.calls[1][1].body as string)).toEqual({
      title: "阶段 4",
      description: "阶段 4",
      priority: null,
      topic: "Tickly",
      status: "new",
      due_at: null,
      parent_id: null,
    })
  })

  it("空更新在发送请求前抛出稳定错误", async () => {
    await expect(updateTask("task-id", {})).rejects.toThrow(
      "至少需要提供一个可更新字段",
    )
    await expect(
      updateTask("task-id", { title: undefined }),
    ).rejects.toThrow("至少需要提供一个可更新字段")

    expect(api.apiFetch).not.toHaveBeenCalled()
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

  it("删除任务时编码任务 ID", async () => {
    api.apiFetch.mockResolvedValue(new Response(null, { status: 204 }))

    await expect(deleteTask("task/id?child")).resolves.toBeUndefined()

    expect(api.apiFetch).toHaveBeenCalledWith(
      "/api/v1/tasks/task%2Fid%3Fchild",
      { method: "DELETE" },
    )
  })

  it("非成功响应转换为安全 ApiError", async () => {
    api.apiFetch.mockResolvedValue(
      jsonResponse(
        { error: { code: "task_not_found", message: "任务不存在" } },
        404,
      ),
    )

    await expect(deleteTask("missing")).rejects.toEqual(
      expect.objectContaining({
        status: 404,
        code: "task_not_found",
        message: "任务不存在",
      }),
    )
  })
})
