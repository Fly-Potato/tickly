import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode, type ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { apiFetch, invalidateAuthentication, setAccessToken } from "./auth-api"
import { AuthProvider, useAuth } from "./auth-context"

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function Probe() {
  const { state, login, logout, completePasswordChange } = useAuth()
  const label =
    state.status === "authenticated"
      ? `authenticated:${state.user.username}`
      : state.status === "anonymous" && state.notice
        ? `anonymous:${state.notice}`
        : state.status === "anonymous" && state.error
          ? `anonymous:${state.error}`
          : state.status

  return (
    <div>
      <span>{label}</span>
      <button
        type="button"
        onClick={() => {
          void login("Potato", "test-password").catch(() => undefined)
        }}
      >
        登录
      </button>
      <button type="button" onClick={() => logout()}>
        退出
      </button>
      <button type="button" onClick={completePasswordChange}>
        密码修改完成
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
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
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
      vi.fn(async () =>
        jsonResponse({ error: { code: "refresh_required" } }, 401)
      )
    )

    renderProvider()

    expect(await screen.findByText("anonymous")).toBeInTheDocument()
  })

  it("登录成功后只把 access token 保存在内存", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
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
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
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

  it("修改密码完成后只清除内存认证状态并给出重新登录提示", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
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
      if (url.endsWith("/example")) {
        return jsonResponse({ ok: true })
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderProvider()
    await screen.findByText("authenticated:potato")
    const completedRestoreCalls = fetchMock.mock.calls.length

    await user.click(screen.getByRole("button", { name: "密码修改完成" }))

    expect(
      await screen.findByText("anonymous:密码已修改，请重新登录")
    ).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(completedRestoreCalls)
    await apiFetch("/api/v1/example")
    const protectedInit = fetchMock.mock.calls.at(-1)?.[1]
    expect(new Headers(protectedInit?.headers).has("Authorization")).toBe(false)
    expect(localStorage).toHaveLength(0)
    expect(sessionStorage).toHaveLength(0)
  })

  it("从密码修改提示发起登录时立即清除旧提示", async () => {
    let resolveLogin!: (response: Response) => void
    const pendingLogin = new Promise<Response>((resolve) => {
      resolveLogin = resolve
    })
    let meCalls = 0
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        return jsonResponse({
          access_token: "restored-access",
          token_type: "bearer",
          expires_in: 900,
        })
      }
      if (url.endsWith("/login")) {
        return pendingLogin
      }
      if (url.endsWith("/me")) {
        meCalls += 1
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
    await screen.findByText("authenticated:potato")

    await user.click(screen.getByRole("button", { name: "密码修改完成" }))
    await screen.findByText("anonymous:密码已修改，请重新登录")
    await user.click(screen.getByRole("button", { name: "登录" }))

    expect(await screen.findByText("anonymous")).toBeInTheDocument()
    expect(screen.queryByText(/密码已修改/)).not.toBeInTheDocument()
    expect(meCalls).toBe(1)

    await act(async () => {
      resolveLogin(
        jsonResponse({
          access_token: "new-access",
          token_type: "bearer",
          expires_in: 900,
        })
      )
    })
    expect(await screen.findByText("authenticated:potato")).toBeInTheDocument()
    expect(meCalls).toBe(2)
  })

  it("提示状态下登录失败后只保留凭据错误", async () => {
    let resolveLogin!: (response: Response) => void
    const pendingLogin = new Promise<Response>((resolve) => {
      resolveLogin = resolve
    })
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        return jsonResponse({
          access_token: "restored-access",
          token_type: "bearer",
          expires_in: 900,
        })
      }
      if (url.endsWith("/login")) {
        return pendingLogin
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
    await screen.findByText("authenticated:potato")

    await user.click(screen.getByRole("button", { name: "密码修改完成" }))
    await screen.findByText("anonymous:密码已修改，请重新登录")
    await user.click(screen.getByRole("button", { name: "登录" }))

    expect(await screen.findByText("anonymous")).toBeInTheDocument()
    await act(async () => {
      resolveLogin(
        jsonResponse(
          { error: { code: "invalid_credentials", message: "认证失败" } },
          401
        )
      )
    })
    expect(
      await screen.findByText("anonymous:用户名或密码错误")
    ).toBeInTheDocument()
    expect(screen.queryByText(/密码已修改/)).not.toBeInTheDocument()
  })

  it("密码修改提示会保留到新的登录尝试，不被后续认证失败覆盖", async () => {
    let refreshCalls = 0
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        refreshCalls += 1
        if (refreshCalls === 1) {
          return jsonResponse({
            access_token: "restored-access",
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
        return jsonResponse({ error: { code: "authentication_required" } }, 401)
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderProvider()
    await screen.findByText("authenticated:potato")

    await user.click(screen.getByRole("button", { name: "密码修改完成" }))
    await screen.findByText("anonymous:密码已修改，请重新登录")
    await act(async () => {
      await apiFetch("/api/v1/example").catch(() => undefined)
    })

    expect(
      await screen.findByText("anonymous:密码已修改，请重新登录")
    ).toBeInTheDocument()
  })

  it.each(["success", "failure"] as const)(
    "修改密码会隔离此前在途 refresh 的 %s 结果",
    async (refreshResult) => {
      let resolveRefresh!: (response: Response) => void
      const pendingRefresh = new Promise<Response>((resolve) => {
        resolveRefresh = resolve
      })
      let refreshCalls = 0
      let protectedCalls = 0
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          refreshCalls += 1
          if (refreshCalls === 1) {
            return jsonResponse({
              access_token: "restored-access",
              token_type: "bearer",
              expires_in: 900,
            })
          }
          return pendingRefresh
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
          protectedCalls += 1
          return jsonResponse(
            { error: { code: "authentication_required" } },
            401
          )
        }
        if (url.endsWith("/after-password-change")) {
          return jsonResponse({ ok: true })
        }
        throw new Error(`未预期的请求：${url}`)
      })
      vi.stubGlobal("fetch", fetchMock)
      const user = userEvent.setup()
      renderProvider()
      await screen.findByText("authenticated:potato")

      const staleRequest = apiFetch("/api/v1/example").catch(() => undefined)
      await waitFor(() => expect(refreshCalls).toBe(2))
      await user.click(screen.getByRole("button", { name: "密码修改完成" }))
      await screen.findByText("anonymous:密码已修改，请重新登录")

      await act(async () => {
        resolveRefresh(
          refreshResult === "success"
            ? jsonResponse({
                access_token: "stale-access",
                token_type: "bearer",
                expires_in: 900,
              })
            : jsonResponse({ error: { code: "refresh_required" } }, 401)
        )
        await staleRequest
      })

      expect(
        screen.getByText("anonymous:密码已修改，请重新登录")
      ).toBeInTheDocument()
      expect(protectedCalls).toBe(1)
      await apiFetch("/api/v1/after-password-change")
      const afterInit = fetchMock.mock.calls.at(-1)?.[1]
      expect(new Headers(afterInit?.headers).has("Authorization")).toBe(false)
      expect(
        screen.getByText("anonymous:密码已修改，请重新登录")
      ).toBeInTheDocument()
    }
  )

  it.each(["success", "failure"] as const)(
    "修改密码会隔离此前在途 login 的 %s 结果",
    async (loginResult) => {
      let resolveLogin!: (response: Response) => void
      const pendingLogin = new Promise<Response>((resolve) => {
        resolveLogin = resolve
      })
      let meCalls = 0
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          return jsonResponse({
            access_token: "restored-access",
            token_type: "bearer",
            expires_in: 900,
          })
        }
        if (url.endsWith("/login")) {
          return pendingLogin
        }
        if (url.endsWith("/me")) {
          meCalls += 1
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
      await screen.findByText("authenticated:potato")

      await user.click(screen.getByRole("button", { name: "登录" }))
      await screen.findByText("anonymous")
      await user.click(screen.getByRole("button", { name: "密码修改完成" }))
      await screen.findByText("anonymous:密码已修改，请重新登录")

      await act(async () => {
        resolveLogin(
          loginResult === "success"
            ? jsonResponse({
                access_token: "stale-login-access",
                token_type: "bearer",
                expires_in: 900,
              })
            : jsonResponse(
                { error: { code: "invalid_credentials", message: "认证失败" } },
                401
              )
        )
      })

      expect(
        screen.getByText("anonymous:密码已修改，请重新登录")
      ).toBeInTheDocument()
      expect(meCalls).toBe(1)
    }
  )

  it.each(["密码修改完成", "退出"] as const)(
    "%s 会隔离此前在途的会话恢复结果",
    async (action) => {
      let resolveRestore!: (response: Response) => void
      const pendingRestore = new Promise<Response>((resolve) => {
        resolveRestore = resolve
      })
      let meCalls = 0
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          return pendingRestore
        }
        if (url.endsWith("/logout")) {
          return new Response(null, { status: 204 })
        }
        if (url.endsWith("/me")) {
          meCalls += 1
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
      await waitFor(() =>
        expect(
          fetchMock.mock.calls.some(([url]) => String(url).endsWith("/refresh"))
        ).toBe(true)
      )

      await user.click(screen.getByRole("button", { name: action }))
      if (action === "密码修改完成") {
        await screen.findByText("anonymous:密码已修改，请重新登录")
      } else {
        await screen.findByText("anonymous")
      }
      await act(async () => {
        resolveRestore(
          jsonResponse({
            access_token: "stale-restore-access",
            token_type: "bearer",
            expires_in: 900,
          })
        )
      })

      expect(screen.queryByText("authenticated:potato")).not.toBeInTheDocument()
      expect(meCalls).toBe(0)
      if (action === "密码修改完成") {
        expect(
          screen.getByText("anonymous:密码已修改，请重新登录")
        ).toBeInTheDocument()
      }
    }
  )

  it("StrictMode 重放 effect 时复用同一个在途 refresh", async () => {
    let resolveRefresh!: (response: Response) => void
    const pendingRefresh = new Promise<Response>((resolve) => {
      resolveRefresh = resolve
    })
    let refreshCalls = 0
    let meCalls = 0
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        refreshCalls += 1
        return refreshCalls === 1
          ? pendingRefresh
          : jsonResponse({ error: { code: "refresh_replayed" } }, 401)
      }
      if (url.endsWith("/me")) {
        meCalls += 1
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

    render(
      <StrictMode>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </StrictMode>
    )

    await waitFor(() => expect(refreshCalls).toBeGreaterThanOrEqual(1))
    expect(refreshCalls).toBe(1)
    await act(async () => {
      resolveRefresh(
        jsonResponse({
          access_token: "restored-access",
          token_type: "bearer",
          expires_in: 900,
        })
      )
    })

    expect(await screen.findByText("authenticated:potato")).toBeInTheDocument()
    expect(refreshCalls).toBe(1)
    expect(meCalls).toBe(1)
  })

  it.each(["success", "failure"] as const)(
    "StrictMode 真卸载会隔离在途 refresh 的 %s 结果",
    async (refreshResult) => {
      let resolveRefresh!: (response: Response) => void
      const pendingRefresh = new Promise<Response>((resolve) => {
        resolveRefresh = resolve
      })
      let refreshCalls = 0
      let meCalls = 0
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          refreshCalls += 1
          return refreshCalls === 1
            ? pendingRefresh
            : jsonResponse({ error: { code: "refresh_replayed" } }, 401)
        }
        if (url.endsWith("/me")) {
          meCalls += 1
          return jsonResponse({
            id: "user-id",
            username: "potato",
            timezone: "Asia/Shanghai",
            is_active: true,
          })
        }
        if (url.endsWith("/after-strict-unmount")) {
          return jsonResponse({ ok: true })
        }
        throw new Error(`未预期的请求：${url}`)
      })
      vi.stubGlobal("fetch", fetchMock)
      const view = render(
        <StrictMode>
          <AuthProvider>
            <Probe />
          </AuthProvider>
        </StrictMode>
      )
      await waitFor(() => expect(refreshCalls).toBeGreaterThanOrEqual(1))
      expect(refreshCalls).toBe(1)

      view.unmount()
      await act(async () => Promise.resolve())
      await act(async () => {
        resolveRefresh(
          refreshResult === "success"
            ? jsonResponse({
                access_token: "stale-restore-access",
                token_type: "bearer",
                expires_in: 900,
              })
            : jsonResponse({ error: { code: "refresh_required" } }, 401)
        )
      })
      await apiFetch("/api/v1/after-strict-unmount")

      expect(meCalls).toBe(0)
      const afterInit = fetchMock.mock.calls.at(-1)?.[1]
      expect(new Headers(afterInit?.headers).has("Authorization")).toBe(false)
    }
  )

  it("卸载 Provider 会隔离此前在途的会话恢复结果", async () => {
    let resolveRestore!: (response: Response) => void
    const pendingRestore = new Promise<Response>((resolve) => {
      resolveRestore = resolve
    })
    let meCalls = 0
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/refresh")) {
        return pendingRestore
      }
      if (url.endsWith("/me")) {
        meCalls += 1
        return jsonResponse({
          id: "user-id",
          username: "potato",
          timezone: "Asia/Shanghai",
          is_active: true,
        })
      }
      if (url.endsWith("/after-unmount")) {
        return jsonResponse({ ok: true })
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const view = renderProvider()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())

    view.unmount()
    await act(async () => {
      resolveRestore(
        jsonResponse({
          access_token: "stale-restore-access",
          token_type: "bearer",
          expires_in: 900,
        })
      )
    })
    await apiFetch("/api/v1/after-unmount")

    expect(meCalls).toBe(0)
    const afterInit = fetchMock.mock.calls.at(-1)?.[1]
    expect(new Headers(afterInit?.headers).has("Authorization")).toBe(false)
  })

  it.each([false, true])(
    "自动 refresh %s 后不能继续保持已认证界面",
    async (refreshSucceeds) => {
      let refreshCalls = 0
      const fetchMock = vi.fn<typeof fetch>(async (input) => {
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
            401
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
    }
  )
})

describe("apiFetch", () => {
  it("旧代 refresh 的 finally 不会清除新代共享请求", async () => {
    setAccessToken("expired-access")
    const attempts = new Map<string, number>()
    let refreshCalls = 0
    let resolveOldRefresh!: (response: Response) => void
    let resolveNewRefresh!: (response: Response) => void
    const oldRefresh = new Promise<Response>((resolve) => {
      resolveOldRefresh = resolve
    })
    const newRefresh = new Promise<Response>((resolve) => {
      resolveNewRefresh = resolve
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/refresh")) {
          refreshCalls += 1
          return refreshCalls === 1 ? oldRefresh : newRefresh
        }
        const attempt = (attempts.get(url) ?? 0) + 1
        attempts.set(url, attempt)
        return attempt === 1
          ? jsonResponse({ error: { code: "authentication_required" } }, 401)
          : jsonResponse({ ok: true })
      })
    )

    const staleRequest = apiFetch("/api/v1/old").catch(() => undefined)
    await waitFor(() => expect(refreshCalls).toBe(1))
    invalidateAuthentication()
    const firstCurrentRequest = apiFetch("/api/v1/current-1")
    await waitFor(() => expect(refreshCalls).toBe(2))

    await act(async () => {
      resolveOldRefresh(
        jsonResponse({
          access_token: "stale-access",
          token_type: "bearer",
          expires_in: 900,
        })
      )
      await staleRequest
    })
    const secondCurrentRequest = apiFetch("/api/v1/current-2")
    await waitFor(() => expect(attempts.get("/api/v1/current-2")).toBe(1))
    expect(refreshCalls).toBe(2)

    await act(async () => {
      resolveNewRefresh(
        jsonResponse({
          access_token: "current-access",
          token_type: "bearer",
          expires_in: 900,
        })
      )
    })
    const responses = await Promise.all([
      firstCurrentRequest,
      secondCurrentRequest,
    ])

    expect(responses.every((response) => response.ok)).toBe(true)
    expect(refreshCalls).toBe(2)
  })

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
            401
          )
        }
        return jsonResponse({ ok: true })
      })
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
        })
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
        return jsonResponse({ error: { code: "authentication_required" } }, 401)
      })
    )

    const response = await apiFetch("/api/v1/example")

    expect(response.status).toBe(401)
    expect(protectedCalls).toBe(2)
  })
})
