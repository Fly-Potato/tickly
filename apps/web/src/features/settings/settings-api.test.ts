import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { setAccessToken } from "@/features/auth/auth-api"
import {
  changePassword,
  createMcpToken,
  listMcpTokens,
  revokeMcpToken,
} from "./settings-api"

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function requestHeaders(init: RequestInit | undefined) {
  return new Headers(init?.headers)
}

beforeEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setAccessToken("web-access")
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  setAccessToken(null)
  localStorage.clear()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

describe("设置 API 客户端", () => {
  it("修改密码只发送当前密码和新密码，并使用内存 access token", async () => {
    const response = new Response(null, { status: 204 })
    const jsonSpy = vi.spyOn(response, "json")
    const fetchMock = vi.fn<typeof fetch>(async () => response)
    vi.stubGlobal("fetch", fetchMock)

    await expect(
      changePassword("current password", "new password")
    ).resolves.toBeUndefined()

    const [url, init = {}] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/account/password")
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin" })
    expect(requestHeaders(init).get("Content-Type")).toBe("application/json")
    expect(requestHeaders(init).get("Authorization")).toBe("Bearer web-access")
    expect(JSON.parse(init.body as string)).toEqual({
      current_password: "current password",
      new_password: "new password",
    })
    expect(jsonSpy).not.toHaveBeenCalled()
  })

  it("读取当前用户的 MCP Token 列表", async () => {
    const item = {
      id: "token-id",
      name: "家中 Codex",
      status: "active",
      expires_at: null,
      last_used_at: null,
      created_at: "2026-08-25T00:00:00Z",
    }
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [item] })
    )
    vi.stubGlobal("fetch", fetchMock)

    await expect(listMcpTokens()).resolves.toEqual([item])

    const [url, init = {}] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/mcp-tokens")
    expect(init).toMatchObject({ credentials: "same-origin" })
    expect(init.method).toBeUndefined()
    expect(requestHeaders(init).get("Authorization")).toBe("Bearer web-access")
  })

  it("创建 MCP Token 只返回一次明文且不写入浏览器存储", async () => {
    const rawToken = "仅用于本测试的一次性明文"
    const item = {
      id: "token-id",
      name: "家中 Codex",
      status: "active",
      expires_at: null,
      last_used_at: null,
      created_at: "2026-08-25T00:00:00Z",
    }
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ item, token: rawToken }, 201)
    )
    vi.stubGlobal("fetch", fetchMock)

    const created = await createMcpToken("家中 Codex", null)

    expect(created.item).toEqual(item)
    expect(created.token === rawToken).toBe(true)
    expect(localStorage).toHaveLength(0)
    expect(sessionStorage).toHaveLength(0)
    const [url, init = {}] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/mcp-tokens")
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin" })
    expect(requestHeaders(init).get("Content-Type")).toBe("application/json")
    expect(requestHeaders(init).get("Authorization")).toBe("Bearer web-access")
    expect(JSON.parse(init.body as string)).toEqual({
      name: "家中 Codex",
      expires_in_days: null,
    })
  })

  it("撤销时编码 Token ID，成功后不解析 204 响应体", async () => {
    const response = new Response(null, { status: 204 })
    const jsonSpy = vi.spyOn(response, "json")
    const fetchMock = vi.fn<typeof fetch>(async () => response)
    vi.stubGlobal("fetch", fetchMock)

    await expect(revokeMcpToken("token/id?next")).resolves.toBeUndefined()

    const [url, init = {}] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/mcp-tokens/token%2Fid%3Fnext")
    expect(init).toMatchObject({ method: "DELETE", credentials: "same-origin" })
    expect(requestHeaders(init).get("Authorization")).toBe("Bearer web-access")
    expect(jsonSpy).not.toHaveBeenCalled()
  })

  it.each([
    ["changePassword", () => changePassword("current", "new")],
    ["listMcpTokens", () => listMcpTokens()],
    ["createMcpToken", () => createMcpToken("家中 Codex", 365)],
    ["revokeMcpToken", () => revokeMcpToken("missing")],
  ])("%s 将非 JSON 错误转换为稳定 ApiError", async (_name, request) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("不得暴露的内部响应", { status: 500 }))
    )

    await expect(request()).rejects.toMatchObject({
      status: 500,
      code: "request_failed",
      message: "请求失败",
    })
  })
})
