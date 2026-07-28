import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { apiFetch, setAccessToken } from "./auth-api"
import { AuthProvider, useAuth } from "./auth-context"

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function Probe() {
  const { state, login, logout } = useAuth()
  const label =
    state.status === "authenticated"
      ? `authenticated:${state.user.username}`
      : state.status

  return (
    <div>
      <span>{label}</span>
      <button type="button" onClick={() => login("Potato", "test-password")}>
        登录
      </button>
      <button type="button" onClick={() => logout()}>
        退出
      </button>
    </div>
  )
}

function renderProvider(children: ReactNode = <Probe />) {
  return render(<AuthProvider>{children}</AuthProvider>)
}

beforeEach(() => {
  setAccessToken(null)
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
})


describe("AuthProvider", () => {
  it("通过 refresh 和 me 恢复认证状态", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        return jsonResponse({
          access_token: "restored-access",
          token_type: "bearer",
          expires_in: 900,
        })
      }
      if (url.endsWith("/me")) {
        return jsonResponse({
          id: "user-id",
          username: "potato",
          timezone: "Asia/Shanghai",
          is_active: true,
        })
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    renderProvider()

    expect(await screen.findByText("authenticated:potato")).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("初始化 refresh 失败后进入匿名状态", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "refresh_required" } }, 401)),
    )

    renderProvider()

    expect(await screen.findByText("anonymous")).toBeInTheDocument()
  })

  it("登录成功后只把 access token 保存在内存", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        return jsonResponse({ error: { code: "refresh_required" } }, 401)
      }
      if (url.endsWith("/login")) {
        return jsonResponse({
          access_token: "login-access",
          token_type: "bearer",
          expires_in: 900,
        })
      }
      if (url.endsWith("/me")) {
        return jsonResponse({
          id: "user-id",
          username: "potato",
          timezone: "Asia/Shanghai",
          is_active: true,
        })
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderProvider()
    await screen.findByText("anonymous")

    await user.click(screen.getByRole("button", { name: "登录" }))

    expect(await screen.findByText("authenticated:potato")).toBeInTheDocument()
    expect(localStorage).toHaveLength(0)
    expect(sessionStorage).toHaveLength(0)
  })

  it("登出请求失败也会清除本地认证状态", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        return jsonResponse({
          access_token: "restored-access",
          token_type: "bearer",
          expires_in: 900,
        })
      }
      if (url.endsWith("/me")) {
        return jsonResponse({
          id: "user-id",
          username: "potato",
          timezone: "Asia/Shanghai",
          is_active: true,
        })
      }
      if (url.endsWith("/logout")) {
        throw new Error("网络中断")
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderProvider()
    await screen.findByText("authenticated:potato")

    await user.click(screen.getByRole("button", { name: "退出" }))

    expect(await screen.findByText("anonymous")).toBeInTheDocument()
  })

  it.each([false, true])(
    "自动 refresh %s 后不能继续保持已认证界面",
    async (refreshSucceeds) => {
      let refreshCalls = 0
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          refreshCalls += 1
          if (refreshCalls === 1 || refreshSucceeds) {
            return jsonResponse({
              access_token: `access-${refreshCalls}`,
              token_type: "bearer",
              expires_in: 900,
            })
          }
          return jsonResponse({ error: { code: "refresh_required" } }, 401)
        }
        if (url.endsWith("/me")) {
          return jsonResponse({
            id: "user-id",
            username: "potato",
            timezone: "Asia/Shanghai",
            is_active: true,
          })
        }
        if (url.endsWith("/example")) {
          return jsonResponse(
            { error: { code: "authentication_required" } },
            401,
          )
        }
        throw new Error(`未预期的请求：${url}`)
      })
      vi.stubGlobal("fetch", fetchMock)
      renderProvider()
      await screen.findByText("authenticated:potato")

      await act(async () => {
        await apiFetch("/api/v1/example").catch(() => undefined)
      })

      expect(await screen.findByText("anonymous")).toBeInTheDocument()
      expect(refreshCalls).toBe(2)
    },
  )
})


describe("apiFetch", () => {
  it("并发认证失败共享一个 refresh 请求", async () => {
    setAccessToken("expired-access")
    let refreshCalls = 0
    const attempts = new Map<string, number>()
    let resolveRefresh!: (response: Response) => void
    const pendingRefresh = new Promise<Response>((resolve) => {
      resolveRefresh = resolve
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          refreshCalls += 1
          return pendingRefresh
        }
        const attempt = (attempts.get(url) ?? 0) + 1
        attempts.set(url, attempt)
        if (attempt === 1) {
          return jsonResponse(
            { error: { code: "authentication_required" } },
            401,
          )
        }
        return jsonResponse({ ok: true })
      }),
    )

    const first = apiFetch("/api/v1/example")
    const second = apiFetch("/api/v1/example-2")
    await waitFor(() => expect(refreshCalls).toBe(1))
    await act(async () => {
      resolveRefresh(
        jsonResponse({
          access_token: "new-access",
          token_type: "bearer",
          expires_in: 900,
        }),
      )
    })
    const responses = await Promise.all([first, second])

    expect(refreshCalls).toBe(1)
    expect(responses.every((response) => response.ok)).toBe(true)
  })

  it("认证失败后最多重试原请求一次", async () => {
    setAccessToken("expired-access")
    let protectedCalls = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          return jsonResponse({
            access_token: "new-access",
            token_type: "bearer",
            expires_in: 900,
          })
        }
        protectedCalls += 1
        return jsonResponse(
          { error: { code: "authentication_required" } },
          401,
        )
      }),
    )

    const response = await apiFetch("/api/v1/example")

    expect(response.status).toBe(401)
    expect(protectedCalls).toBe(2)
  })
})
