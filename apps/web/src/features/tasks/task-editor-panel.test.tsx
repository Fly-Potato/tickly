import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Task } from "./task-api"
import { TaskEditorPanel } from "./task-editor-panel"

const task: Task = {
  id: "task-id",
  title: "完成阶段 4",
  notes: "保留说明",
  is_completed: false,
  priority: "medium",
  due_at: "2026-07-30T10:00:00Z",
  completed_at: null,
  created_at: "2026-07-28T08:00:00Z",
  updated_at: "2026-07-28T08:00:00Z",
}

function renderEditor(overrides: Partial<Parameters<typeof TaskEditorPanel>[0]> = {}) {
  const props: Parameters<typeof TaskEditorPanel>[0] = {
    task,
    timeZone: "Asia/Shanghai",
    saving: false,
    deleting: false,
    error: null,
    onDirtyChange: vi.fn(),
    onSave: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    ...overrides,
  }
  render(<TaskEditorPanel {...props} />)
  return props
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Todo 编辑面板", () => {
  it("一次保存只提交真实修改并支持显式清空", async () => {
    const user = userEvent.setup()
    const props = renderEditor()

    await user.clear(screen.getByLabelText("备注"))
    await user.selectOptions(screen.getByLabelText("优先级"), "high")
    await user.click(screen.getByRole("button", { name: "保存" }))

    expect(props.onSave).toHaveBeenCalledWith({
      notes: null,
      priority: "high",
    })
  })

  it("不存在的夏令时墙上时间显示字段错误且不提交", async () => {
    const user = userEvent.setup()
    const props = renderEditor({ timeZone: "America/Los_Angeles" })
    const dueInput = screen.getByLabelText("截止时间")

    fireEvent.change(dueInput, { target: { value: "2026-03-08T02:30" } })
    await user.click(screen.getByRole("button", { name: "保存" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("夏令时")
    expect(props.onSave).not.toHaveBeenCalled()
  })

  it("脏表单按 Escape 时确认放弃并可取消关闭", async () => {
    const user = userEvent.setup()
    const props = renderEditor()
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)

    await user.type(screen.getByLabelText("标题"), " 修改")
    await user.keyboard("{Escape}")

    expect(confirm).toHaveBeenCalledWith("放弃未保存的修改？")
    expect(props.onClose).not.toHaveBeenCalled()
    expect(screen.getByRole("heading", { name: "编辑任务" })).toBeInTheDocument()
  })

  it("删除需要显示任务标题的独立确认", async () => {
    const user = userEvent.setup()
    const props = renderEditor()

    await user.click(screen.getByRole("button", { name: "删除任务" }))
    expect(
      screen.getByRole("heading", { name: "删除任务？" }),
    ).toBeInTheDocument()
    expect(screen.getByText(/完成阶段 4/)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "确认删除" }))
    expect(props.onDelete).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole("heading", { name: "删除任务？" }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "编辑任务" })).toBeInTheDocument()
  })

  it("没有修改时保存禁用且 API 错误保留面板", () => {
    renderEditor({ error: "任务保存失败" })

    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("任务保存失败")
    expect(screen.getByRole("heading", { name: "编辑任务" })).toBeInTheDocument()
  })
})
