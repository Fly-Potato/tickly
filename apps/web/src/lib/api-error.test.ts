import { describe, expect, it } from "vitest"

import { ApiError, errorCode, responseError, safeErrorMessage } from "./api-error"

function jsonResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("API 错误边界", () => {
  it("读取统一错误 envelope 且不消耗原响应", async () => {
    const response = jsonResponse(
      { error: { code: "task_not_found", message: "任务不存在" } },
      404,
    )

    expect(await errorCode(response)).toBe("task_not_found")
    await expect(responseError(response)).resolves.toMatchObject({
      status: 404,
      code: "task_not_found",
      message: "任务不存在",
    })
    await expect(response.json()).resolves.toEqual({
      error: { code: "task_not_found", message: "任务不存在" },
    })
  })

  it("非 JSON 响应和未知异常只显示安全兜底", async () => {
    const error = await responseError(new Response("secret", { status: 500 }))

    expect(error).toEqual(
      expect.objectContaining({ status: 500, code: "request_failed" }),
    )
    expect(safeErrorMessage(error, "操作失败")).toBe("请求失败")
    expect(safeErrorMessage(new Error("secret"), "操作失败")).toBe("操作失败")
    expect(error).toBeInstanceOf(ApiError)
  })
})
