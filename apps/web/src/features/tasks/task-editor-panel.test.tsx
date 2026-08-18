import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  listParentOptions: vi.fn(),
}))

vi.mock("./task-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./task-api")>()),
  ...api,
}))

import type { ParentTaskOption, Task } from "./task-api"
import { TaskEditorPanel } from "./task-editor-panel"

const task: Task = {
  id: "task-id",
  serial: 42,
  title: "完成阶段 4",
  description: "保留说明",
  priority: "medium",
  topic: "Tickly",
  status: "new",
  due_at: "2026-07-30T10:00:00Z",
  completed_at: null,
  parent_id: null,
  created_at: "2026-07-28T08:00:00Z",
  updated_at: "2026-07-28T08:00:00Z",
}

const parentOption: ParentTaskOption = {
  id: "parent-7",
  serial: 7,
  title: "父任务候选",
  topic: "Tickly",
  status: "in_progress",
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function renderEditor(
  overrides: Partial<Parameters<typeof TaskEditorPanel>[0]> = {}
) {
  const props: Parameters<typeof TaskEditorPanel>[0] = {
    task,
    childCount: 0,
    currentParent: null,
    timeZone: "Asia/Shanghai",
    saving: false,
    deleting: false,
    creatingChild: false,
    error: null,
    onDirtyChange: vi.fn(),
    onSave: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onCreateChild: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    ...overrides,
  }
  const rendered = render(<TaskEditorPanel {...props} />)
  return {
    ...props,
    rerenderEditor(next: Partial<Parameters<typeof TaskEditorPanel>[0]>) {
      rendered.rerender(<TaskEditorPanel {...props} {...next} />)
    },
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  api.listParentOptions.mockReset()
  api.listParentOptions.mockResolvedValue({ items: [], next_cursor: null })
})

describe("Todo 编辑面板", () => {
  it.each([
    ["标题", "标题不能为空"],
    ["描述", "描述不能为空"],
    ["主题", "主题不能为空"],
  ])("%s 清空时显示字段错误且不提交", async (label, message) => {
    const user = userEvent.setup()
    const props = renderEditor()

    await user.clear(screen.getByLabelText(label))
    await user.click(screen.getByRole("button", { name: "保存" }))

    expect(screen.getByText(message)).toBeInTheDocument()
    expect(props.onSave).not.toHaveBeenCalled()
  })

  it("一次保存只提交规范化后的真实修改", async () => {
    const user = userEvent.setup()
    const props = renderEditor()

    await user.clear(screen.getByLabelText("描述"))
    await user.type(screen.getByLabelText("描述"), "新的详细说明")
    await user.clear(screen.getByLabelText("主题"))
    await user.type(screen.getByLabelText("主题"), "工作")
    await user.selectOptions(screen.getByLabelText("优先级"), "")
    await user.selectOptions(screen.getByLabelText("状态"), "in_progress")
    await user.click(screen.getByRole("button", { name: "保存" }))

    expect(props.onSave).toHaveBeenCalledWith({
      description: "新的详细说明",
      topic: "工作",
      priority: null,
      status: "in_progress",
    })
  })

  it("保存失败后重新显示错误并保留用户修改", async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockRejectedValue(new Error("网络中断"))
    const props = renderEditor({ onSave })

    await user.clear(screen.getByLabelText("描述"))
    await user.type(screen.getByLabelText("描述"), "失败后保留的描述")
    await user.clear(screen.getByLabelText("主题"))
    await user.type(screen.getByLabelText("主题"), "失败后保留的主题")
    fireEvent.change(screen.getByLabelText("截止时间"), {
      target: { value: "2026-08-01T18:30" },
    })
    await user.click(screen.getByRole("button", { name: "保存" }))

    expect(onSave).toHaveBeenCalledOnce()
    props.rerenderEditor({ error: "任务保存失败" })
    expect(screen.getByRole("alert")).toHaveTextContent("任务保存失败")
    expect(
      screen.getByRole("heading", { name: "编辑任务" })
    ).toBeInTheDocument()
    expect(screen.getByLabelText("描述")).toHaveValue("失败后保留的描述")
    expect(screen.getByLabelText("主题")).toHaveValue("失败后保留的主题")
    expect(screen.getByLabelText("截止时间")).toHaveValue("2026-08-01T18:30")
  })

  it("打开父级选择器读取第一页，过滤自身并只在保存时提交选择", async () => {
    api.listParentOptions.mockResolvedValue({
      items: [
        {
          id: task.id,
          serial: task.serial,
          title: task.title,
          topic: task.topic,
          status: task.status,
        },
        parentOption,
      ],
      next_cursor: null,
    })
    const user = userEvent.setup()
    const props = renderEditor()

    await user.click(screen.getByRole("button", { name: "选择父待办" }))

    expect(api.listParentOptions).toHaveBeenCalledWith(
      { limit: 20 },
      expect.any(AbortSignal)
    )
    expect(
      await screen.findByRole("button", {
        name: "选择 #7 父任务候选 作为父待办",
      })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", {
        name: "选择 #42 完成阶段 4 作为父待办",
      })
    ).not.toBeInTheDocument()

    await user.click(
      screen.getByRole("button", {
        name: "选择 #7 父任务候选 作为父待办",
      })
    )
    expect(props.onSave).not.toHaveBeenCalled()
    expect(screen.getByText("#7 父任务候选")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "保存" }))
    expect(props.onSave).toHaveBeenCalledWith({ parent_id: "parent-7" })
  })

  it("父级查询变化会取消旧请求并在 250ms 后使用完整查询", async () => {
    let firstSignal: AbortSignal | undefined
    api.listParentOptions
      .mockImplementationOnce((_query, signal?: AbortSignal) => {
        firstSignal = signal
        return new Promise(() => undefined)
      })
      .mockResolvedValueOnce({ items: [], next_cursor: null })
    const user = userEvent.setup()
    renderEditor()

    await user.click(screen.getByRole("button", { name: "选择父待办" }))
    await user.type(screen.getByRole("searchbox", { name: "搜索父待办" }), "#7")

    await waitFor(() =>
      expect(api.listParentOptions).toHaveBeenLastCalledWith(
        { query: "#7", limit: 20 },
        expect.any(AbortSignal)
      )
    )
    expect(firstSignal?.aborted).toBe(true)
  })

  it("父级查询进入防抖窗口时立即废弃旧分页上下文", async () => {
    api.listParentOptions.mockResolvedValue({
      items: [parentOption],
      next_cursor: "old-cursor",
    })
    const user = userEvent.setup()
    renderEditor()

    await user.click(screen.getByRole("button", { name: "选择父待办" }))
    expect(
      await screen.findByRole("button", { name: "加载更多父待办" })
    ).toBeInTheDocument()

    vi.useFakeTimers()
    try {
      fireEvent.change(screen.getByRole("searchbox", { name: "搜索父待办" }), {
        target: { value: "#7" },
      })
      const staleLoadMore = screen.queryByRole("button", {
        name: "加载更多父待办",
      })
      if (staleLoadMore !== null) fireEvent.click(staleLoadMore)

      const sentStaleRequest = api.listParentOptions.mock.calls.some(
        ([request]) => request.query === "#7" && request.cursor === "old-cursor"
      )
      expect({
        loadMoreVisible: staleLoadMore !== null,
        sentStaleRequest,
      }).toEqual({ loadMoreVisible: false, sentStaleRequest: false })
    } finally {
      vi.useRealTimers()
    }
  })

  it("解除父待办只修改草稿并在保存时提交 null", async () => {
    const user = userEvent.setup()
    const props = renderEditor({
      task: { ...task, parent_id: parentOption.id },
      currentParent: parentOption,
    })

    expect(screen.getByText("#7 父任务候选")).toBeInTheDocument()
    expect(screen.queryByText("parent-7")).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "解除父待办" }))

    expect(props.onSave).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "保存" }))
    expect(props.onSave).toHaveBeenCalledWith({ parent_id: null })
  })

  it("拥有子待办的根任务禁用父级选择并说明一层关系边界", () => {
    renderEditor({ childCount: 1 })

    expect(screen.getByRole("button", { name: "选择父待办" })).toBeDisabled()
    expect(
      screen.getByText("一层父子关系下，拥有子待办的任务不能再成为子待办")
    ).toBeInTheDocument()
  })

  it("父级候选支持错误重试、空态、分页追加和按 ID 去重", async () => {
    api.listParentOptions
      .mockRejectedValueOnce(new Error("secret parent detail"))
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValueOnce({ items: [parentOption], next_cursor: "next" })
      .mockResolvedValueOnce({
        items: [
          parentOption,
          { ...parentOption, id: "parent-8", serial: 8, title: "另一候选" },
        ],
        next_cursor: null,
      })
    const user = userEvent.setup()
    renderEditor()

    await user.click(screen.getByRole("button", { name: "选择父待办" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("父待办加载失败")
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "重试父待办" }))
    expect(await screen.findByText("没有可选的父待办")).toBeInTheDocument()

    await user.type(screen.getByRole("searchbox", { name: "搜索父待办" }), "父")
    expect(
      await screen.findByRole("button", {
        name: "选择 #7 父任务候选 作为父待办",
      })
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "加载更多父待办" }))

    expect(api.listParentOptions).toHaveBeenLastCalledWith(
      { query: "父", cursor: "next", limit: 20 },
      expect.any(AbortSignal)
    )
    expect(
      screen.getAllByRole("button", {
        name: "选择 #7 父任务候选 作为父待办",
      })
    ).toHaveLength(1)
    expect(
      await screen.findByRole("button", {
        name: "选择 #8 另一候选 作为父待办",
      })
    ).toBeInTheDocument()
  })

  it("同一 render 连续加载更多只发送一个 cursor 请求", async () => {
    const nextPage = deferred<{
      items: ParentTaskOption[]
      next_cursor: string | null
    }>()
    api.listParentOptions
      .mockResolvedValueOnce({ items: [parentOption], next_cursor: "next" })
      .mockReturnValue(nextPage.promise)
    const user = userEvent.setup()
    renderEditor()

    await user.click(screen.getByRole("button", { name: "选择父待办" }))
    const loadMore = await screen.findByRole("button", {
      name: "加载更多父待办",
    })
    act(() => {
      loadMore.dispatchEvent(new MouseEvent("click", { bubbles: true }))
      loadMore.dispatchEvent(new MouseEvent("click", { bubbles: true }))
    })
    const cursorRequestCount = api.listParentOptions.mock.calls.filter(
      ([request]) => request.cursor === "next"
    ).length

    await act(async () => {
      nextPage.resolve({ items: [], next_cursor: null })
      await nextPage.promise
    })
    expect(cursorRequestCount).toBe(1)
  })

  it("根任务创建子待办时继承主题，成功后清空标题并保留主题", async () => {
    const user = userEvent.setup()
    const props = renderEditor()
    const title = screen.getByLabelText("子待办标题")
    const topic = screen.getByLabelText("子待办主题")

    expect(topic).toHaveValue("Tickly")
    await user.type(title, "  补充测试  ")
    await user.clear(topic)
    await user.type(topic, "  工作  {Enter}")

    expect(props.onCreateChild).toHaveBeenCalledWith({
      title: "补充测试",
      topic: "工作",
      parent_id: task.id,
    })
    await waitFor(() => expect(title).toHaveValue(""))
    expect(topic).toHaveValue("工作")
  })

  it.each([
    ["仍为旧父主题", false],
    ["已经清空", true],
  ])("父主题更新时同步%s的子主题并使用新值提交", async (_, clearTopic) => {
    const user = userEvent.setup()
    const props = renderEditor({ task: { ...task, topic: "工作" } })
    const title = screen.getByLabelText("子待办标题")
    const topic = screen.getByLabelText("子待办主题")

    if (clearTopic) await user.clear(topic)
    props.rerenderEditor({ task: { ...task, topic: "Personal" } })

    expect(topic).toHaveValue("Personal")
    await user.type(title, "跟随父主题")
    await user.click(screen.getByRole("button", { name: "添加子待办" }))
    expect(props.onCreateChild).toHaveBeenCalledWith({
      title: "跟随父主题",
      topic: "Personal",
      parent_id: task.id,
    })
  })

  it("父主题更新不覆盖用户自定义子主题并按草稿提交", async () => {
    const user = userEvent.setup()
    const props = renderEditor({ task: { ...task, topic: "工作" } })
    const title = screen.getByLabelText("子待办标题")
    const topic = screen.getByLabelText("子待办主题")

    await user.clear(topic)
    await user.type(topic, "自定义")
    props.rerenderEditor({ task: { ...task, topic: "Personal" } })

    expect(topic).toHaveValue("自定义")
    await user.type(title, "保留自定义主题")
    await user.click(screen.getByRole("button", { name: "添加子待办" }))
    expect(props.onCreateChild).toHaveBeenCalledWith({
      title: "保留自定义主题",
      topic: "自定义",
      parent_id: task.id,
    })
  })

  it("子待办创建失败保留字段、隐藏敏感错误且创建中防止重复提交", async () => {
    const onCreateChild = vi.fn().mockRejectedValue(new Error("secret detail"))
    const user = userEvent.setup()
    const props = renderEditor({ onCreateChild })
    const title = screen.getByLabelText("子待办标题")
    const topic = screen.getByLabelText("子待办主题")

    await user.type(title, "保留标题")
    await user.clear(topic)
    await user.type(topic, "保留主题{Enter}")

    expect(await screen.findByRole("alert")).toHaveTextContent("子待办创建失败")
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument()
    expect(title).toHaveValue("保留标题")
    expect(topic).toHaveValue("保留主题")
    props.rerenderEditor({ creatingChild: true })
    expect(title).toBeDisabled()
    expect(topic).toBeDisabled()
    expect(
      screen.getByRole("button", { name: "正在添加子待办" })
    ).toBeDisabled()
  })

  it("子待办创建期间禁用编辑器保存和删除", async () => {
    const user = userEvent.setup()
    renderEditor({ creatingChild: true })

    await user.type(screen.getByLabelText("标题"), " 修改")

    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "删除任务" })).toBeDisabled()
  })

  it.each(["saving", "deleting"] as const)(
    "%s 期间禁用子待办字段和提交但不显示创建中提示",
    (pendingState) => {
      renderEditor({ [pendingState]: true })

      expect(screen.getByLabelText("子待办标题")).toBeDisabled()
      expect(screen.getByLabelText("子待办主题")).toBeDisabled()
      expect(screen.getByRole("button", { name: "添加子待办" })).toBeDisabled()
      expect(
        screen.queryByRole("button", { name: "正在添加子待办" })
      ).not.toBeInTheDocument()
    }
  )

  it("同一 render 连续提交子待办只执行一次并在失败后保留草稿", async () => {
    const creation = deferred<void>()
    const onCreateChild = vi.fn().mockReturnValue(creation.promise)
    const user = userEvent.setup()
    renderEditor({ onCreateChild })
    const title = screen.getByLabelText("子待办标题")
    const topic = screen.getByLabelText("子待办主题")
    const form = screen
      .getByRole("button", { name: "添加子待办" })
      .closest("form")

    await user.type(title, "保留标题")
    await user.clear(topic)
    await user.type(topic, "保留主题")
    expect(form).not.toBeNull()
    fireEvent.submit(form!)
    fireEvent.submit(form!)

    await act(async () => {
      creation.reject(new Error("secret duplicate detail"))
      await expect(creation.promise).rejects.toThrow("secret duplicate detail")
    })

    expect(onCreateChild).toHaveBeenCalledOnce()
    expect(await screen.findByRole("alert")).toHaveTextContent("子待办创建失败")
    expect(title).toHaveValue("保留标题")
    expect(topic).toHaveValue("保留主题")
  })

  it("子任务不显示添加子待办表单", () => {
    renderEditor({
      task: { ...task, parent_id: parentOption.id },
      currentParent: parentOption,
    })

    expect(screen.queryByLabelText("子待办标题")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "添加子待办" })
    ).not.toBeInTheDocument()
  })

  it("按约定顺序展示可编辑字段和只读元数据", () => {
    renderEditor({
      task: {
        ...task,
        status: "completed",
        completed_at: "2026-07-30T11:00:00Z",
      },
    })

    const fields = [
      screen.getByText("#42"),
      screen.getByLabelText("标题"),
      screen.getByLabelText("描述"),
      screen.getByLabelText("主题"),
      screen.getByLabelText("状态"),
      screen.getByLabelText("优先级"),
      screen.getByLabelText("截止时间"),
      screen.getByText("父待办"),
      screen.getByText(/创建 ·/),
      screen.getByText(/完成 ·/),
    ]
    for (const [index, field] of fields.entries()) {
      const next = fields[index + 1]
      if (next !== undefined) {
        expect(
          field.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy()
      }
    }
    expect(screen.queryByLabelText("完成时间")).not.toBeInTheDocument()
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
    expect(
      screen.getByRole("heading", { name: "编辑任务" })
    ).toBeInTheDocument()
  })

  it("删除需要显示任务标题的独立确认", async () => {
    const user = userEvent.setup()
    const props = renderEditor({ childCount: 2 })

    await user.click(screen.getByRole("button", { name: "删除任务" }))
    expect(
      screen.getByRole("heading", { name: "删除任务？" })
    ).toBeInTheDocument()
    expect(screen.getByText(/完成阶段 4/)).toBeInTheDocument()
    expect(
      screen.getByText(/2 个子待办不会被删除，将成为顶层待办/)
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "确认删除" }))
    expect(props.onDelete).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole("heading", { name: "删除任务？" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "编辑任务" })
    ).toBeInTheDocument()
  })

  it("没有修改时保存禁用且面板保持打开", () => {
    renderEditor()

    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled()
    expect(
      screen.getByRole("heading", { name: "编辑任务" })
    ).toBeInTheDocument()
  })
})
