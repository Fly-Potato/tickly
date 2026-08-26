/// <reference types="node" />

import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { App } from "@/App"
import { LoginForm } from "./login-form"

const originalLocation = `${window.location.pathname}${window.location.search}${window.location.hash}`
const originalHistoryState = window.history.state
const appStyles = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8")

const auth = vi.hoisted(() => ({
  state: { status: "anonymous" } as
    | { status: "initializing" }
    | { status: "anonymous"; error?: string; notice?: string }
    | {
        status: "authenticated"
        user: {
          id: string
          username: string
          timezone: string
          is_active: boolean
        }
      },
  login: vi.fn(),
  logout: vi.fn(),
  completePasswordChange: vi.fn(),
}))

vi.mock("./auth-context", () => ({
  useAuth: () => auth,
}))

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

beforeEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
  auth.state = { status: "anonymous" }
  auth.login.mockReset()
  auth.logout.mockReset()
  auth.completePasswordChange.mockReset()
})

afterEach(() => {
  window.history.replaceState(originalHistoryState, "", originalLocation)
})

describe("LoginForm", () => {
  it("通过 Enter 提交凭据并在请求期间阻止重复登录", async () => {
    let resolveLogin!: () => void
    auth.login.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveLogin = resolve
        })
    )
    const user = userEvent.setup()
    render(<LoginForm />)

    await user.type(screen.getByLabelText("用户名"), "Potato")
    await user.type(
      screen.getByLabelText("密码"),
      "correct horse battery staple{Enter}"
    )

    expect(auth.login).toHaveBeenCalledWith(
      "Potato",
      "correct horse battery staple"
    )
    expect(screen.getByRole("button", { name: "正在登录" })).toBeDisabled()
    resolveLogin()
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "登录" })).toBeEnabled()
    )
  })

  it("登录失败显示统一提示且不回显凭据", async () => {
    auth.login.mockRejectedValue(new Error("secret database detail"))
    const user = userEvent.setup()
    render(<LoginForm />)

    await user.type(screen.getByLabelText("用户名"), "potato")
    await user.type(screen.getByLabelText("密码"), "wrong password")
    await user.click(screen.getByRole("button", { name: "登录" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "用户名或密码错误"
    )
    expect(screen.queryByText("secret database detail")).not.toBeInTheDocument()
    expect(screen.queryByText("wrong password")).not.toBeInTheDocument()
  })

  it("密码修改提示使用独立状态消息而不是凭据错误", () => {
    auth.state = {
      status: "anonymous",
      notice: "密码已修改，请重新登录",
    }

    render(<LoginForm />)

    expect(screen.getByRole("status")).toHaveTextContent(
      "密码已修改，请重新登录"
    )
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.getAllByRole("status")).toHaveLength(1)
  })

  it("新的失败登录会用凭据错误替换密码修改提示", async () => {
    auth.state = {
      status: "anonymous",
      notice: "密码已修改，请重新登录",
    }
    auth.login.mockRejectedValue(new Error("认证失败"))
    const user = userEvent.setup()
    render(<LoginForm />)

    await user.type(screen.getByLabelText("用户名"), "potato")
    await user.type(screen.getByLabelText("密码"), "wrong password")
    await user.click(screen.getByRole("button", { name: "登录" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "用户名或密码错误"
    )
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("新的成功登录状态不会继续显示密码修改提示", async () => {
    auth.state = {
      status: "anonymous",
      notice: "密码已修改，请重新登录",
    }
    auth.login.mockImplementation(async () => {
      auth.state = {
        status: "authenticated",
        user: {
          id: "user-id",
          username: "potato",
          timezone: "Asia/Shanghai",
          is_active: true,
        },
      }
    })
    const user = userEvent.setup()
    const view = render(<LoginForm />)

    await user.type(screen.getByLabelText("用户名"), "potato")
    await user.type(screen.getByLabelText("密码"), "correct password")
    await user.click(screen.getByRole("button", { name: "登录" }))
    view.rerender(<LoginForm />)

    expect(screen.queryByRole("status")).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
})

describe("App 认证状态门", () => {
  function useAuthenticatedState() {
    auth.state = {
      status: "authenticated",
      user: {
        id: "user-id",
        username: "potato",
        timezone: "Asia/Shanghai",
        is_active: true,
      },
    }
  }

  function stubTaskRequests() {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === "/api/v1/mcp-tokens") {
        return jsonResponse({ items: [] })
      }
      if (url === "/api/v1/tasks/topics") {
        return jsonResponse({ items: [] })
      }
      if (url.startsWith("/api/v1/tasks?")) {
        return jsonResponse({ items: [], next_cursor: null })
      }
      throw new Error(`未预期的请求：${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    return fetchMock
  }

  function taskRequestCount(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/v1/tasks")
    ).length
  }

  it("共享品牌按钮保持至少 44px 的键盘与触控点击区域", () => {
    expect(appStyles).toMatch(
      /\.todo-brand-button\s*\{[^}]*min-h-11[^}]*min-w-11/s
    )
  })

  it("初始化时显示明确的恢复状态", () => {
    auth.state = { status: "initializing" }

    render(<App />)

    expect(screen.getByRole("status")).toHaveTextContent("正在恢复登录状态")
  })

  it("匿名状态显示用户名登录页", () => {
    render(<App />)

    expect(
      screen.getByRole("heading", { name: "登录 Tickly" })
    ).toBeInTheDocument()
  })

  it("共享 Header 在待办和设置页之间保持并按页面挂载任务请求", async () => {
    useAuthenticatedState()
    auth.logout.mockResolvedValue(undefined)
    const fetchMock = stubTaskRequests()
    const user = userEvent.setup()
    render(<App />)

    const header = screen.getByRole("banner")
    expect(header).toContainElement(screen.getByText("potato"))
    expect(header).toContainElement(screen.getByText("Asia/Shanghai"))
    expect(
      screen.getByRole("heading", { name: "Todo list" })
    ).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const settingsButton = screen.getByRole("button", { name: "设置" })
    settingsButton.focus()
    await user.keyboard("{Enter}")

    expect(window.location.pathname).toBe("/settings")
    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument()
    expect(screen.getByRole("banner")).toBe(header)
    expect(
      screen.queryByRole("heading", { name: "Todo list" })
    ).not.toBeInTheDocument()
    expect(taskRequestCount(fetchMock)).toBe(2)

    await user.click(screen.getByRole("button", { name: "返回待办" }))
    expect(window.location.pathname).toBe("/")
    expect(
      await screen.findByRole("heading", { name: "Todo list" })
    ).toBeInTheDocument()
    await waitFor(() => expect(taskRequestCount(fetchMock)).toBe(4))

    await user.click(screen.getByRole("button", { name: "退出登录" }))
    expect(auth.logout).toHaveBeenCalledOnce()
  })

  it("直接打开设置页不发送任务请求且仍可退出", async () => {
    useAuthenticatedState()
    window.history.replaceState(null, "", "/settings")
    const fetchMock = stubTaskRequests()
    const user = userEvent.setup()

    render(<App />)

    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument()
    expect(screen.getAllByText("potato").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Asia/Shanghai").length).toBeGreaterThan(0)
    expect(taskRequestCount(fetchMock)).toBe(0)
    await user.click(screen.getByRole("button", { name: "退出登录" }))
    expect(auth.logout).toHaveBeenCalledOnce()
  })

  it("设置页品牌按钮可用键盘返回待办", async () => {
    useAuthenticatedState()
    window.history.replaceState(null, "", "/settings")
    const fetchMock = stubTaskRequests()
    const user = userEvent.setup()
    render(<App />)

    const brand = screen.getByRole("button", { name: /Tickly/ })
    brand.focus()
    await user.keyboard(" ")

    expect(window.location.pathname).toBe("/")
    expect(
      await screen.findByRole("heading", { name: "Todo list" })
    ).toBeInTheDocument()
    await waitFor(() => expect(taskRequestCount(fetchMock)).toBe(2))
  })

  it("认证后的未知路径会规范为根路径并显示待办", async () => {
    useAuthenticatedState()
    window.history.replaceState(null, "", "/unknown")
    stubTaskRequests()

    render(<App />)

    expect(window.location.pathname).toBe("/")
    expect(
      await screen.findByRole("heading", { name: "Todo list" })
    ).toBeInTheDocument()
  })
})
