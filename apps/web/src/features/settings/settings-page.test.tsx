import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "@/lib/api-error"
import { AuthenticatedShell } from "@/features/auth/authenticated-shell"
import type { AuthUser } from "@/features/auth/auth-api"
import type { McpToken } from "./settings-api"
import { SettingsPage } from "./settings-page"

const api = vi.hoisted(() => ({
  changePassword: vi.fn(),
  listMcpTokens: vi.fn(),
  createMcpToken: vi.fn(),
  revokeMcpToken: vi.fn(),
}))

const auth = vi.hoisted(() => ({
  state: {
    status: "authenticated" as const,
    user: {
      id: "user-id-that-must-stay-private",
      username: "potato",
      timezone: "Asia/Shanghai",
      is_active: true,
    },
  },
  login: vi.fn(),
  logout: vi.fn(),
  completePasswordChange: vi.fn(),
}))

vi.mock("./settings-api", () => api)
vi.mock("@/features/auth/auth-context", () => ({ useAuth: () => auth }))

const AUTH_USER: AuthUser = {
  id: "user-id-that-must-stay-private",
  username: "potato",
  timezone: "Asia/Shanghai",
  is_active: true,
}

const RAW_TOKEN =
  "tickly_mcp_123e4567-e89b-12d3-a456-426614174000." + "a".repeat(43)
const RAW_TOKEN_B =
  "tickly_mcp_123e4567-e89b-12d3-a456-426614174001." + "b".repeat(43)
const appStyles = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8")

const ACTIVE_TOKEN: McpToken = {
  id: "active-token",
  name: "家中 Codex",
  status: "active",
  created_at: "2026-08-25T16:30:00Z",
  expires_at: null,
  last_used_at: null,
}

const originalLocation = `${window.location.pathname}${window.location.search}${window.location.hash}`
const originalHistoryState = window.history.state
const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(
  Navigator.prototype,
  "clipboard"
)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function installClipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  return writeText
}

async function fillPasswordForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("当前密码"), "current password")
  await user.type(screen.getByLabelText("新密码"), "new secure password")
  await user.type(screen.getByLabelText("确认新密码"), "new secure password")
}

async function issueToken(
  user: ReturnType<typeof userEvent.setup>,
  name = "家中 Codex"
) {
  await user.type(screen.getByLabelText("Token 名称"), name)
  await user.click(screen.getByRole("button", { name: "创建 Token" }))
  return screen.findByRole("dialog", { name: "保存 MCP Token" })
}

beforeEach(() => {
  api.changePassword.mockReset()
  api.listMcpTokens.mockReset().mockResolvedValue([])
  api.createMcpToken.mockReset()
  api.revokeMcpToken.mockReset()
  auth.logout.mockReset().mockResolvedValue(undefined)
  auth.completePasswordChange.mockReset()
  auth.state = { status: "authenticated", user: { ...AUTH_USER } }
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState(null, "", "/")
  installClipboard()
})

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState(originalHistoryState, "", originalLocation)
  if (originalClipboardDescriptor) {
    Object.defineProperty(
      Navigator.prototype,
      "clipboard",
      originalClipboardDescriptor
    )
  } else {
    Reflect.deleteProperty(navigator, "clipboard")
  }
  vi.restoreAllMocks()
})

describe("设置页与密码修改", () => {
  it("展示可扫描的账号摘要但不泄露用户 ID", async () => {
    render(<SettingsPage user={AUTH_USER} />)

    const page = screen.getByRole("region", { name: "设置" })
    expect(within(page).getByRole("heading", { name: "设置" })).toHaveAttribute(
      "id",
      "settings-title"
    )
    expect(page).toHaveTextContent("potato")
    expect(page).toHaveTextContent("Asia/Shanghai")
    expect(page).not.toHaveTextContent(AUTH_USER.id)
    expect(await screen.findByText("还没有 MCP Token")).toBeInTheDocument()
  })

  it("确认密码不一致时不发送请求", async () => {
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    await user.type(screen.getByLabelText("当前密码"), "current password")
    await user.type(screen.getByLabelText("新密码"), "new secure password")
    await user.type(screen.getByLabelText("确认新密码"), "different password")
    await user.click(screen.getByRole("button", { name: "修改密码" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "两次输入的新密码不一致"
    )
    expect(api.changePassword).not.toHaveBeenCalled()
  })

  it("允许符合服务端策略的六字符新密码提交", async () => {
    api.changePassword.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    const newPassword = screen.getByLabelText("新密码")
    const confirmation = screen.getByLabelText("确认新密码")
    expect(newPassword).toHaveAttribute("minlength", "6")
    expect(confirmation).toHaveAttribute("minlength", "6")
    await user.type(screen.getByLabelText("当前密码"), "current password")
    await user.type(newPassword, "123456")
    await user.type(confirmation, "123456")
    await user.click(screen.getByRole("button", { name: "修改密码" }))

    expect(api.changePassword).toHaveBeenCalledWith(
      "current password",
      "123456"
    )
    expect(auth.completePasswordChange).toHaveBeenCalledOnce()
  })

  it("五字符新密码不会提交", async () => {
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    await user.type(screen.getByLabelText("当前密码"), "current password")
    await user.type(screen.getByLabelText("新密码"), "12345")
    await user.type(screen.getByLabelText("确认新密码"), "12345")
    await user.click(screen.getByRole("button", { name: "修改密码" }))

    expect(api.changePassword).not.toHaveBeenCalled()
    expect(auth.completePasswordChange).not.toHaveBeenCalled()
  })

  it("密码请求期间锁定重复提交", async () => {
    const request = deferred<void>()
    api.changePassword.mockReturnValue(request.promise)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await fillPasswordForm(user)

    const submit = screen.getByRole("button", { name: "修改密码" })
    await user.click(submit)
    expect(screen.getByRole("button", { name: "正在修改" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "正在修改" }))
    expect(api.changePassword).toHaveBeenCalledOnce()

    await act(async () => request.resolve())
  })

  it("密码 API 错误只显示安全消息", async () => {
    api.changePassword.mockRejectedValue(
      new ApiError(400, "invalid_password", "当前密码不正确")
    )
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await fillPasswordForm(user)
    await user.click(screen.getByRole("button", { name: "修改密码" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("当前密码不正确")
    expect(screen.queryByText("current password")).not.toBeInTheDocument()
    expect(screen.queryByText("new secure password")).not.toBeInTheDocument()
  })

  it("未知密码错误使用稳定回退且不回显内部响应", async () => {
    api.changePassword.mockRejectedValue(new Error("database secret response"))
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await fillPasswordForm(user)
    await user.click(screen.getByRole("button", { name: "修改密码" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("密码修改失败")
    expect(
      screen.queryByText("database secret response")
    ).not.toBeInTheDocument()
  })

  it("成功响应后先清空三个字段再结束本地会话", async () => {
    api.changePassword.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await fillPasswordForm(user)
    const fields = [
      screen.getByLabelText("当前密码"),
      screen.getByLabelText("新密码"),
      screen.getByLabelText("确认新密码"),
    ] as HTMLInputElement[]
    auth.completePasswordChange.mockImplementation(() => {
      expect(fields.every((field) => field.value === "")).toBe(true)
    })

    await user.click(screen.getByRole("button", { name: "修改密码" }))

    expect(api.changePassword).toHaveBeenCalledWith(
      "current password",
      "new secure password"
    )
    expect(auth.completePasswordChange).toHaveBeenCalledOnce()
  })
})

describe("MCP Token 列表与创建", () => {
  it("列表加载与密码卡片互不阻断", () => {
    api.listMcpTokens.mockReturnValue(new Promise(() => undefined))
    render(<SettingsPage user={AUTH_USER} />)

    expect(screen.getByRole("status")).toHaveTextContent("正在加载 Token")
    expect(screen.getByLabelText("当前密码")).toBeEnabled()
  })

  it("空列表、失败和重试都有明确状态且重试锁定", async () => {
    api.listMcpTokens.mockRejectedValueOnce(
      new ApiError(503, "unavailable", "Token 列表暂时不可用")
    )
    const retry = deferred<McpToken[]>()
    api.listMcpTokens.mockReturnValueOnce(retry.promise)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Token 列表暂时不可用"
    )
    const retryButton = screen.getByRole("button", { name: "重试" })
    await user.click(retryButton)
    expect(screen.getByRole("button", { name: "正在重试" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "正在重试" }))
    expect(api.listMcpTokens).toHaveBeenCalledTimes(2)

    await act(async () => retry.resolve([]))
    expect(await screen.findByText("还没有 MCP Token")).toBeInTheDocument()
  })

  it("卸载后忽略迟到的列表响应", async () => {
    const request = deferred<McpToken[]>()
    api.listMcpTokens.mockReturnValue(request.promise)
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined)
    const view = render(<SettingsPage user={AUTH_USER} />)
    view.unmount()

    await act(async () => request.resolve([ACTIVE_TOKEN]))

    expect(consoleError).not.toHaveBeenCalled()
  })

  it("初次列表快照不会覆盖在途创建且会合并服务端原有项", async () => {
    const request = deferred<McpToken[]>()
    const created = {
      ...ACTIVE_TOKEN,
      id: "123e4567-e89b-12d3-a456-426614174000",
      name: "刚创建的设备",
      created_at: "2026-08-26T00:00:00Z",
    }
    const serverItem = {
      ...ACTIVE_TOKEN,
      id: "223e4567-e89b-12d3-a456-426614174000",
      name: "服务端已有设备",
    }
    api.listMcpTokens.mockReturnValue(request.promise)
    api.createMcpToken.mockResolvedValue({ item: created, token: RAW_TOKEN })
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    const dialog = await issueToken(user, created.name)
    await user.click(
      within(dialog).getByRole("button", { name: "我已保存，关闭" })
    )
    await act(async () => request.resolve([serverItem]))

    const rows = await screen.findAllByTestId("mcp-token-row")
    expect(rows.map((row) => row.querySelector("strong")?.textContent)).toEqual(
      [created.name, serverItem.name]
    )
  })

  it("迟到快照包含刚创建的同 ID Token 时完全采用服务端元数据", async () => {
    const request = deferred<McpToken[]>()
    const created = {
      ...ACTIVE_TOKEN,
      id: "133e4567-e89b-12d3-a456-426614174000",
      name: "本地创建设备",
      created_at: "2026-08-26T00:00:00Z",
      expires_at: null,
      last_used_at: null,
    }
    const serverItem = {
      ...created,
      name: "服务端已更新设备",
      status: "revoked" as const,
      created_at: "2026-08-25T00:00:00Z",
      expires_at: "2026-08-30T00:00:00Z",
      last_used_at: "2026-08-27T00:31:00Z",
    }
    api.listMcpTokens.mockReturnValue(request.promise)
    api.createMcpToken.mockResolvedValue({ item: created, token: RAW_TOKEN })
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    const dialog = await issueToken(user, created.name)
    await user.click(
      within(dialog).getByRole("button", { name: "我已保存，关闭" })
    )
    await act(async () => request.resolve([serverItem, serverItem]))

    const rows = await screen.findAllByTestId("mcp-token-row")
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent(serverItem.name)
    expect(rows[0]).not.toHaveTextContent(created.name)
    expect(rows[0]).toHaveTextContent("已撤销")
    expect(rows[0]).not.toHaveTextContent("永不过期")
    expect(rows[0]).not.toHaveTextContent("从未使用")
    expect(rows[0]).toHaveTextContent(/2026\/8\/27/)
  })

  it("重试快照与在途创建按 ID 去重且保留本地项", async () => {
    const request = deferred<McpToken[]>()
    const created = {
      ...ACTIVE_TOKEN,
      id: "323e4567-e89b-12d3-a456-426614174000",
      name: "重试期间创建",
    }
    const serverItem = {
      ...ACTIVE_TOKEN,
      id: "423e4567-e89b-12d3-a456-426614174000",
      name: "服务端设备",
    }
    api.listMcpTokens
      .mockRejectedValueOnce(new ApiError(503, "unavailable", "暂不可用"))
      .mockReturnValueOnce(request.promise)
    api.createMcpToken.mockResolvedValue({ item: created, token: RAW_TOKEN })
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await screen.findByRole("alert")
    await user.click(screen.getByRole("button", { name: "重试" }))

    const dialog = await issueToken(user, created.name)
    await user.click(
      within(dialog).getByRole("button", { name: "我已保存，关闭" })
    )
    await act(async () => request.resolve([created, created, serverItem]))

    const rows = await screen.findAllByTestId("mcp-token-row")
    expect(rows.map((row) => row.querySelector("strong")?.textContent)).toEqual(
      [created.name, serverItem.name]
    )
  })

  it("旧列表快照不能把在途撤销恢复为 active", async () => {
    const request = deferred<McpToken[]>()
    const created = {
      ...ACTIVE_TOKEN,
      id: "523e4567-e89b-12d3-a456-426614174000",
      name: "撤销竞态设备",
    }
    api.listMcpTokens.mockReturnValue(request.promise)
    api.createMcpToken.mockResolvedValue({ item: created, token: RAW_TOKEN })
    api.revokeMcpToken.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    const issued = await issueToken(user, created.name)
    await user.click(
      within(issued).getByRole("button", { name: "我已保存，关闭" })
    )

    await user.click(
      await screen.findByRole("button", {
        name: /撤销 撤销竞态设备/,
      })
    )
    const revokeDialog = screen.getByRole("dialog", {
      name: "撤销 MCP Token",
    })
    await user.click(
      within(revokeDialog).getByRole("button", { name: "确认撤销" })
    )
    await waitFor(() => expect(screen.getByText("已撤销")).toBeInTheDocument())
    await act(async () => request.resolve([created]))

    expect(screen.getByText("已撤销")).toBeInTheDocument()
    expect(screen.queryByText("有效")).not.toBeInTheDocument()
  })

  it("重试期间撤销只覆盖迟到快照状态并保留服务端更新元数据", async () => {
    const request = deferred<McpToken[]>()
    const created = {
      ...ACTIVE_TOKEN,
      id: "533e4567-e89b-12d3-a456-426614174000",
      name: "本地待撤销设备",
    }
    const serverItem = {
      ...created,
      name: "服务端更新设备",
      status: "active" as const,
      expires_at: "2027-08-27T00:00:00Z",
      last_used_at: "2026-08-27T00:31:00Z",
    }
    api.listMcpTokens
      .mockRejectedValueOnce(new ApiError(503, "unavailable", "暂不可用"))
      .mockReturnValueOnce(request.promise)
    api.createMcpToken.mockResolvedValue({ item: created, token: RAW_TOKEN })
    api.revokeMcpToken.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await screen.findByRole("alert")
    const issued = await issueToken(user, created.name)
    await user.click(
      within(issued).getByRole("button", { name: "我已保存，关闭" })
    )
    await user.click(screen.getByRole("button", { name: "重试" }))

    await user.click(
      screen.getByRole("button", { name: /撤销 本地待撤销设备/ })
    )
    const revokeDialog = screen.getByRole("dialog", {
      name: "撤销 MCP Token",
    })
    await user.click(
      within(revokeDialog).getByRole("button", { name: "确认撤销" })
    )
    await waitFor(() => expect(screen.getByText("已撤销")).toBeInTheDocument())
    await act(async () => request.resolve([serverItem, serverItem]))

    const rows = await screen.findAllByTestId("mcp-token-row")
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent(serverItem.name)
    expect(rows[0]).not.toHaveTextContent(created.name)
    expect(rows[0]).toHaveTextContent("已撤销")
    expect(rows[0]).not.toHaveTextContent("从未使用")
    expect(rows[0]).toHaveTextContent(/2026\/8\/27/)
  })

  it("默认按 365 天创建且阻止重复请求", async () => {
    const request = deferred<{ item: McpToken; token: string }>()
    api.createMcpToken.mockReturnValue(request.promise)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await user.type(screen.getByLabelText("Token 名称"), "  家中 Codex  ")

    await user.click(screen.getByRole("button", { name: "创建 Token" }))
    expect(api.createMcpToken).toHaveBeenCalledWith("家中 Codex", 365)
    expect(screen.getByRole("button", { name: "正在创建" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "正在创建" }))
    expect(api.createMcpToken).toHaveBeenCalledOnce()

    await act(async () =>
      request.resolve({ item: ACTIVE_TOKEN, token: RAW_TOKEN })
    )
    expect(
      await screen.findByRole("dialog", { name: "保存 MCP Token" })
    ).toBeInTheDocument()
  })

  it.each([
    ["90 天", 90],
    ["永不过期", null],
  ] as const)("把 %s 有效期映射为受限 API 值", async (option, expected) => {
    api.createMcpToken.mockResolvedValue({
      item: ACTIVE_TOKEN,
      token: RAW_TOKEN,
    })
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await user.type(screen.getByLabelText("Token 名称"), "设备 Token")
    await user.selectOptions(screen.getByLabelText("有效期"), option)
    await user.click(screen.getByRole("button", { name: "创建 Token" }))

    expect(api.createMcpToken).toHaveBeenCalledWith("设备 Token", expected)
  })

  it("创建错误独立于列表和撤销状态", async () => {
    api.listMcpTokens.mockResolvedValue([ACTIVE_TOKEN])
    api.createMcpToken.mockRejectedValue(new Error("secret internal detail"))
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await user.type(screen.getByLabelText("Token 名称"), "失败设备")
    await user.click(screen.getByRole("button", { name: "创建 Token" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "MCP Token 创建失败"
    )
    expect(screen.getByText("家中 Codex")).toBeInTheDocument()
    expect(screen.queryByText("secret internal detail")).not.toBeInTheDocument()
  })

  it("按 API 顺序显示状态、时区和空时间值，非法时间不会崩溃", async () => {
    api.listMcpTokens.mockResolvedValue([
      ACTIVE_TOKEN,
      {
        ...ACTIVE_TOKEN,
        id: "expired-token",
        name: "过期设备",
        status: "expired",
        created_at: "invalid timestamp",
        expires_at: "2026-08-26T00:00:00Z",
        last_used_at: "2026-08-25T16:31:00Z",
      },
      {
        ...ACTIVE_TOKEN,
        id: "revoked-token",
        name: "撤销设备",
        status: "revoked",
      },
    ])
    render(<SettingsPage user={AUTH_USER} />)

    expect(
      (await screen.findAllByTestId("mcp-token-row")).map(
        (row) => row.querySelector("strong")?.textContent
      )
    ).toEqual(["家中 Codex", "过期设备", "撤销设备"])
    expect(screen.getByText("有效")).toBeInTheDocument()
    expect(screen.getByText("已过期")).toBeInTheDocument()
    expect(screen.getByText("已撤销")).toBeInTheDocument()
    expect(screen.getAllByText("永不过期").length).toBeGreaterThan(0)
    expect(screen.getAllByText("从未使用").length).toBeGreaterThan(0)
    expect(screen.getByText(/时间未知/)).toBeInTheDocument()
    expect(screen.getAllByText(/2026\/8\/26/).length).toBeGreaterThan(0)
    expect(screen.getAllByRole("button", { name: /撤销/ })).toHaveLength(2)
  })
})

describe("一次性 Token Dialog", () => {
  beforeEach(() => {
    api.createMcpToken.mockResolvedValue({
      item: ACTIVE_TOKEN,
      token: RAW_TOKEN,
    })
  })

  it("只在 Dialog 显示原文，复制成功后关闭清除且不持久化", async () => {
    const user = userEvent.setup()
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue(undefined)
    render(<SettingsPage user={AUTH_USER} />)
    const nameInput = screen.getByLabelText("Token 名称")
    const dialog = await issueToken(user)

    expect(RAW_TOKEN).toHaveLength(91)
    expect(dialog).toHaveTextContent(RAW_TOKEN)
    expect(dialog).toHaveTextContent(`${window.location.origin}/mcp`)
    expect(dialog).toHaveTextContent("TICKLY_MCP_TOKEN")
    expect(dialog).toHaveTextContent("Authorization: Bearer <Token>")
    expect(`${window.location.origin}/mcp`).not.toContain(RAW_TOKEN)
    for (const element of Array.from(dialog.querySelectorAll("*"))) {
      for (const attribute of Array.from(element.attributes)) {
        expect(attribute.value).not.toContain(RAW_TOKEN)
      }
    }

    await user.click(within(dialog).getByRole("button", { name: "复制 Token" }))
    expect(writeText).toHaveBeenCalledWith(RAW_TOKEN)
    expect(within(dialog).getByRole("status")).toHaveTextContent("Token 已复制")
    await user.click(
      within(dialog).getByRole("button", { name: "我已保存，关闭" })
    )
    await waitFor(() =>
      expect(screen.queryByText(RAW_TOKEN)).not.toBeInTheDocument()
    )
    expect(document.activeElement).toBe(nameInput)
    expect(localStorage).toHaveLength(0)
    expect(sessionStorage).toHaveLength(0)
    expect(screen.getByText("家中 Codex")).not.toHaveTextContent(RAW_TOKEN)
  })

  it("复制失败显示安全反馈并允许重试", async () => {
    const user = userEvent.setup()
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockRejectedValueOnce(new Error("clipboard secret detail"))
    render(<SettingsPage user={AUTH_USER} />)
    const dialog = await issueToken(user)

    await user.click(within(dialog).getByRole("button", { name: "复制 Token" }))
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "复制失败，请重试"
    )
    expect(dialog).not.toHaveTextContent("clipboard secret detail")
    writeText.mockResolvedValueOnce(undefined)
    await user.click(within(dialog).getByRole("button", { name: "复制 Token" }))
    expect(within(dialog).getByRole("status")).toHaveTextContent("Token 已复制")
  })

  it("Escape 关闭时立即清除原文且不能重新打开", async () => {
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    await issueToken(user)

    await user.keyboard("{Escape}")

    await waitFor(() =>
      expect(screen.queryByText(RAW_TOKEN)).not.toBeInTheDocument()
    )
    expect(
      screen.queryByRole("dialog", { name: "保存 MCP Token" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /重新打开/ })
    ).not.toBeInTheDocument()
  })

  it("点击遮罩关闭时清除原文", async () => {
    const user = userEvent.setup()
    const { container } = render(<SettingsPage user={AUTH_USER} />)
    await issueToken(user)
    const backdrop = document.querySelector(".settings-dialog-backdrop")
    expect(backdrop).not.toBeNull()

    fireEvent.pointerDown(backdrop as Element)
    fireEvent.click(backdrop as Element)

    await waitFor(() =>
      expect(screen.queryByText(RAW_TOKEN)).not.toBeInTheDocument()
    )
    expect(container).not.toHaveTextContent(RAW_TOKEN)
  })

  it.each([
    ["resolve", false],
    ["reject", true],
  ] as const)(
    "Token A 的迟到复制 %s 不会污染 Token B",
    async (_settlement, rejects) => {
      const copy = deferred<void>()
      api.createMcpToken
        .mockResolvedValueOnce({ item: ACTIVE_TOKEN, token: RAW_TOKEN })
        .mockResolvedValueOnce({
          item: {
            ...ACTIVE_TOKEN,
            id: "623e4567-e89b-12d3-a456-426614174000",
            name: "设备 B",
          },
          token: RAW_TOKEN_B,
        })
      const user = userEvent.setup()
      vi.spyOn(navigator.clipboard, "writeText").mockReturnValue(copy.promise)
      render(<SettingsPage user={AUTH_USER} />)
      const dialogA = await issueToken(user, "设备 A")
      await user.click(
        within(dialogA).getByRole("button", { name: "复制 Token" })
      )
      await user.click(
        within(dialogA).getByRole("button", { name: "我已保存，关闭" })
      )
      const dialogB = await issueToken(user, "设备 B")

      await act(async () => {
        if (rejects) {
          copy.reject(new Error("Token A clipboard failure"))
        } else {
          copy.resolve()
        }
      })

      expect(dialogB).toHaveTextContent(RAW_TOKEN_B)
      expect(dialogB).not.toHaveTextContent(RAW_TOKEN)
      expect(within(dialogB).queryByRole("status")).not.toBeInTheDocument()
      expect(within(dialogB).queryByRole("alert")).not.toBeInTheDocument()
      expect(
        within(dialogB).getByRole("button", { name: "复制 Token" })
      ).toBeEnabled()
    }
  )

  it.each(["resolve", "reject"] as const)(
    "卸载后忽略复制请求的迟到 %s",
    async (settlement) => {
      const copy = deferred<void>()
      const user = userEvent.setup()
      vi.spyOn(navigator.clipboard, "writeText").mockReturnValue(copy.promise)
      const consoleError = vi
        .spyOn(console, "error")
        .mockImplementation(() => undefined)
      const view = render(<SettingsPage user={AUTH_USER} />)
      const dialog = await issueToken(user)
      await user.click(
        within(dialog).getByRole("button", { name: "复制 Token" })
      )
      view.unmount()

      await act(async () => {
        if (settlement === "reject") {
          copy.reject(new Error("late clipboard failure"))
        } else {
          copy.resolve()
        }
      })

      expect(consoleError).not.toHaveBeenCalled()
    }
  )
})

describe("Token 稳定短 ID 与响应式边界", () => {
  it("同名 Token 使用短 ID 区分行、操作和撤销目标", async () => {
    const first = {
      ...ACTIVE_TOKEN,
      id: "123e4567-e89b-12d3-a456-426614174000",
      name: "同名设备",
    }
    const second = {
      ...ACTIVE_TOKEN,
      id: "abcdef01-e89b-12d3-a456-426614174000",
      name: "同名设备",
    }
    api.listMcpTokens.mockResolvedValue([first, second])
    api.revokeMcpToken.mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)

    expect(
      await screen.findByRole("listitem", {
        name: "MCP Token：同名设备，ID 123e4567",
      })
    ).toHaveTextContent("ID 123e4567")
    expect(
      screen.getByRole("listitem", {
        name: "MCP Token：同名设备，ID abcdef01",
      })
    ).toHaveTextContent("ID abcdef01")
    await user.click(
      screen.getByRole("button", {
        name: "撤销 同名设备，ID abcdef01",
      })
    )
    const dialog = screen.getByRole("dialog", { name: "撤销 MCP Token" })
    expect(dialog).toHaveTextContent("ID abcdef01")
    await user.click(within(dialog).getByRole("button", { name: "确认撤销" }))

    expect(api.revokeMcpToken).toHaveBeenCalledWith(second.id)
    expect(api.revokeMcpToken).not.toHaveBeenCalledWith(first.id)
  })

  it("Token 四列布局只在 md 启用且长撤销名称允许强制换行", () => {
    const rowRule = appStyles.match(/\.settings-token-row\s*\{[^}]*\}/s)?.[0]
    expect(rowRule).toContain("md:grid-cols-")
    expect(rowRule).not.toContain("sm:grid-cols-")
    expect(appStyles).toMatch(
      /\.settings-revoke-description\s*\{[^}]*(overflow-wrap:\s*anywhere|word-break:\s*break-word)/s
    )
  })
})

describe("撤销 MCP Token", () => {
  it("确认时显示名称、锁定重复操作并更新为已撤销", async () => {
    api.listMcpTokens.mockResolvedValue([ACTIVE_TOKEN])
    const request = deferred<void>()
    api.revokeMcpToken.mockReturnValue(request.promise)
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    const revokeButton = await screen.findByRole("button", {
      name: "撤销 家中 Codex，ID active-t",
    })
    const tokenRow = screen.getByRole("listitem", {
      name: "MCP Token：家中 Codex，ID active-t",
    })
    expect(tokenRow).toHaveAttribute("tabindex", "-1")
    await user.click(revokeButton)
    const dialog = screen.getByRole("dialog", { name: "撤销 MCP Token" })
    expect(dialog).toHaveTextContent("家中 Codex")
    expect(dialog).toHaveTextContent("无法撤销")

    await user.click(within(dialog).getByRole("button", { name: "确认撤销" }))
    expect(
      within(dialog).getByRole("button", { name: "正在撤销" })
    ).toBeDisabled()
    await user.click(within(dialog).getByRole("button", { name: "正在撤销" }))
    expect(api.revokeMcpToken).toHaveBeenCalledOnce()
    expect(api.revokeMcpToken).toHaveBeenCalledWith(ACTIVE_TOKEN.id)

    await act(async () => request.resolve())
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "撤销 MCP Token" })
      ).not.toBeInTheDocument()
    )
    expect(screen.getByText("已撤销")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", {
        name: "撤销 家中 Codex，ID active-t",
      })
    ).not.toBeInTheDocument()
    expect(document.activeElement).toBe(tokenRow)
  })

  it("失败时保持 Dialog、显示独立安全错误并恢复操作", async () => {
    api.listMcpTokens.mockResolvedValue([ACTIVE_TOKEN])
    api.revokeMcpToken.mockRejectedValue(new Error("database secret detail"))
    const user = userEvent.setup()
    render(<SettingsPage user={AUTH_USER} />)
    const revokeButton = await screen.findByRole("button", {
      name: "撤销 家中 Codex，ID active-t",
    })
    await user.click(revokeButton)
    const dialog = screen.getByRole("dialog", { name: "撤销 MCP Token" })
    await user.click(within(dialog).getByRole("button", { name: "确认撤销" }))

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "MCP Token 撤销失败"
    )
    expect(dialog).not.toHaveTextContent("database secret detail")
    expect(
      within(dialog).getByRole("button", { name: "确认撤销" })
    ).toBeEnabled()
    await user.click(within(dialog).getByRole("button", { name: "取消" }))
    await waitFor(() => expect(document.activeElement).toBe(revokeButton))
  })
})

describe("认证外壳集成", () => {
  it("设置分支渲染完整页面并保留共享 Header", async () => {
    window.history.replaceState(null, "", "/settings")
    render(<AuthenticatedShell />)

    expect(
      screen
        .getAllByRole("banner")
        .some((element) => element.classList.contains("todo-header"))
    ).toBe(true)
    expect(screen.getByRole("region", { name: "设置" })).toBeInTheDocument()
    expect(screen.getByLabelText("当前密码")).toBeInTheDocument()
    expect(await screen.findByText("还没有 MCP Token")).toBeInTheDocument()
  })
})
/// <reference types="node" />

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
