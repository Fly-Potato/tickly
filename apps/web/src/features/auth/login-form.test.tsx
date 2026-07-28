import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { App } from "@/App"
import { LoginForm } from "./login-form"

const auth = vi.hoisted(() => ({
  state: { status: "anonymous" } as
    | { status: "initializing" }
    | { status: "anonymous"; error?: string }
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
}))

vi.mock("./auth-context", () => ({
  useAuth: () => auth,
}))

beforeEach(() => {
  auth.state = { status: "anonymous" }
  auth.login.mockReset()
  auth.logout.mockReset()
})


describe("LoginForm", () => {
  it("通过 Enter 提交凭据并在请求期间阻止重复登录", async () => {
    let resolveLogin!: () => void
    auth.login.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveLogin = resolve
        }),
    )
    const user = userEvent.setup()
    render(<LoginForm />)

    await user.type(screen.getByLabelText("用户名"), "Potato")
    await user.type(
      screen.getByLabelText("密码"),
      "correct horse battery staple{Enter}",
    )

    expect(auth.login).toHaveBeenCalledWith(
      "Potato",
      "correct horse battery staple",
    )
    expect(screen.getByRole("button", { name: "正在登录" })).toBeDisabled()
    resolveLogin()
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "登录" })).toBeEnabled(),
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
      "用户名或密码错误",
    )
    expect(screen.queryByText("secret database detail")).not.toBeInTheDocument()
    expect(screen.queryByText("wrong password")).not.toBeInTheDocument()
  })
})


describe("App 认证状态门", () => {
  it("初始化时显示明确的恢复状态", () => {
    auth.state = { status: "initializing" }

    render(<App />)

    expect(screen.getByRole("status")).toHaveTextContent("正在恢复登录状态")
  })

  it("匿名状态显示用户名登录页", () => {
    render(<App />)

    expect(
      screen.getByRole("heading", { name: "登录 Tickly" }),
    ).toBeInTheDocument()
  })

  it("认证状态显示用户名并可以退出", async () => {
    auth.state = {
      status: "authenticated",
      user: {
        id: "user-id",
        username: "potato",
        timezone: "Asia/Shanghai",
        is_active: true,
      },
    }
    auth.logout.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText("potato")).toBeInTheDocument()
    expect(screen.getByText("认证已就绪")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "退出登录" }))
    expect(auth.logout).toHaveBeenCalledOnce()
  })
})
