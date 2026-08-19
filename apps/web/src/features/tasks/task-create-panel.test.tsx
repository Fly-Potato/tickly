import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { TaskCreatePanelProps } from "./task-create-panel"
import { TaskCreatePanel } from "./task-create-panel"

function renderPanel(overrides: Partial<TaskCreatePanelProps> = {}) {
  const props: TaskCreatePanelProps = {
    selectedTopic: "Tickly",
    topicOptions: ["Tickly", "工作"],
    timeZone: "Asia/Shanghai",
    creating: false,
    onCreate: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    ...overrides,
  }
  render(<TaskCreatePanel {...props} />)
  return props
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("新建待办抽屉", () => {
  it("预填当前主题并提交规范化后的完整创建参数", async () => {
    const user = userEvent.setup()
    const props = renderPanel()

    expect(screen.getByLabelText("主题")).toHaveValue("Tickly")
    await user.type(screen.getByLabelText("标题"), "  表格化列表  ")
    await user.type(screen.getByLabelText("描述（可选）"), "  调整主视图  ")
    await user.selectOptions(screen.getByLabelText("优先级"), "medium")
    fireEvent.change(screen.getByLabelText("截止时间"), {
      target: { value: "2026-08-20T18:00" },
    })
    await user.click(screen.getByRole("button", { name: "创建待办" }))

    expect(props.onCreate).toHaveBeenCalledWith({
      title: "表格化列表",
      description: "调整主视图",
      topic: "Tickly",
      priority: "medium",
      due_at: "2026-08-20T10:00:00.000Z",
    })
    expect(props.onClose).toHaveBeenCalledOnce()
  })

  it("标题和主题为空时显示字段错误且不提交", async () => {
    const user = userEvent.setup()
    const props = renderPanel({ selectedTopic: undefined })

    await user.click(screen.getByRole("button", { name: "创建待办" }))

    expect(screen.getByText("标题不能为空")).toBeInTheDocument()
    expect(screen.getByText("主题不能为空")).toBeInTheDocument()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it("可选字段为空时只提交标题和主题", async () => {
    const user = userEvent.setup()
    const props = renderPanel()

    await user.type(screen.getByLabelText("标题"), "最小任务")
    await user.click(screen.getByRole("button", { name: "创建待办" }))

    expect(props.onCreate).toHaveBeenCalledWith({
      title: "最小任务",
      topic: "Tickly",
    })
  })

  it("创建失败时显示安全错误并保留输入", async () => {
    const user = userEvent.setup()
    const props = renderPanel({
      onCreate: vi.fn().mockRejectedValue(new Error("secret detail")),
    })

    await user.type(screen.getByLabelText("标题"), "保留的标题")
    await user.type(screen.getByLabelText("描述（可选）"), "保留的描述")
    await user.click(screen.getByRole("button", { name: "创建待办" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("任务创建失败")
    expect(screen.getByLabelText("标题")).toHaveValue("保留的标题")
    expect(screen.getByLabelText("描述（可选）")).toHaveValue("保留的描述")
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("仅预填主题时关闭不触发确认", async () => {
    const user = userEvent.setup()
    const props = renderPanel()
    const confirm = vi.spyOn(window, "confirm")

    await user.keyboard("{Escape}")

    expect(confirm).not.toHaveBeenCalled()
    expect(props.onClose).toHaveBeenCalledOnce()
  })

  it("真实修改关闭前确认并可取消", async () => {
    const user = userEvent.setup()
    const props = renderPanel()
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)

    await user.type(screen.getByLabelText("标题"), "未提交")
    await user.keyboard("{Escape}")

    expect(confirm).toHaveBeenCalledWith("放弃未保存的修改？")
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("创建中禁用字段和关闭操作", async () => {
    const user = userEvent.setup()
    const props = renderPanel({ creating: true })

    expect(screen.getByRole("button", { name: "正在创建" })).toBeDisabled()
    expect(screen.getByLabelText("标题")).toBeDisabled()
    await user.keyboard("{Escape}")
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("焦点保持在新建抽屉内", async () => {
    const user = userEvent.setup()
    renderPanel()
    const dialog = screen.getByRole("dialog", { name: "新建待办" })

    await waitFor(() =>
      expect(dialog).toContainElement(document.activeElement as HTMLElement)
    )
    await user.tab({ shift: true })
    const activeElement = document.activeElement as HTMLElement
    expect(
      dialog.contains(activeElement) ||
        activeElement.hasAttribute("data-base-ui-focus-guard")
    ).toBe(true)
  })
})
