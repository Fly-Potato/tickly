import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderToString } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useAppNavigation, type AuthenticatedPage } from "./app-navigation"

const originalLocation = `${window.location.pathname}${window.location.search}${window.location.hash}`
const originalHistoryState = window.history.state
const navigationEventName = "tickly:navigation"

function NavigationProbe({
  onRender,
}: {
  onRender?(navigate: (page: AuthenticatedPage) => void): void
}) {
  const { page, navigate } = useAppNavigation()
  onRender?.(navigate)

  return (
    <div>
      <span aria-label="当前页面">{page}</span>
      <button type="button" onClick={() => navigate("tasks")}>
        待办
      </button>
      <button type="button" onClick={() => navigate("settings")}>
        设置
      </button>
    </div>
  )
}

function dispatchPath(path: string) {
  act(() => {
    window.history.replaceState(null, "", path)
    window.dispatchEvent(new PopStateEvent("popstate"))
  })
}

beforeEach(() => {
  window.history.replaceState(null, "", "/")
})

afterEach(() => {
  vi.restoreAllMocks()
  window.history.replaceState(originalHistoryState, "", originalLocation)
})

describe("认证后页面导航", () => {
  it("直接打开设置路径时立即显示设置页", () => {
    window.history.replaceState(null, "", "/settings")
    render(<NavigationProbe />)
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("settings")
    expect(window.location.pathname).toBe("/settings")
  })

  it("使用 History API 在待办和设置页之间导航", async () => {
    const pushState = vi.spyOn(window.history, "pushState")
    const user = userEvent.setup()
    render(<NavigationProbe />)

    await user.click(screen.getByRole("button", { name: "设置" }))
    expect(window.location.pathname).toBe("/settings")
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("settings")

    await user.click(screen.getByRole("button", { name: "待办" }))
    expect(window.location.pathname).toBe("/")
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("tasks")
    expect(pushState).toHaveBeenNthCalledWith(1, null, "", "/settings")
    expect(pushState).toHaveBeenNthCalledWith(2, null, "", "/")
  })

  it("响应后退和前进产生的 popstate", () => {
    render(<NavigationProbe />)
    dispatchPath("/settings")
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("settings")
    dispatchPath("/")
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("tasks")
  })

  it("render 阶段只解析页面且不修改未知路径", () => {
    window.history.replaceState(null, "", "/missing")
    const replaceState = vi.spyOn(window.history, "replaceState")
    const markup = renderToString(<NavigationProbe />)
    expect(markup).toContain("tasks")
    expect(replaceState).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/missing")
  })

  it("初始未知路径在 effect 中规范为根路径", () => {
    window.history.replaceState(null, "", "/missing")
    render(<NavigationProbe />)
    expect(window.location.pathname).toBe("/")
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("tasks")
  })

  it("popstate 遇到未知路径时规范为根路径", () => {
    render(<NavigationProbe />)
    dispatchPath("/missing")
    expect(window.location.pathname).toBe("/")
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("tasks")
  })

  it("重复导航不会增加 history entry 且仍校准页面", async () => {
    window.history.replaceState(null, "", "/settings")
    const pushState = vi.spyOn(window.history, "pushState")
    const user = userEvent.setup()
    render(<NavigationProbe />)
    await user.click(screen.getByRole("button", { name: "设置" }))
    expect(pushState).not.toHaveBeenCalled()
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("settings")
  })

  it("一个实例导航时同步所有已挂载实例", async () => {
    const user = userEvent.setup()
    render(
      <>
        <NavigationProbe />
        <NavigationProbe />
      </>
    )
    await user.click(screen.getAllByRole("button", { name: "设置" })[0])
    expect(screen.getAllByLabelText("当前页面")).toHaveLength(2)
    expect(
      screen
        .getAllByLabelText("当前页面")
        .every((page) => page.textContent === "settings")
    ).toBe(true)
  })

  it("同路径导航不 push 并校准所有实例的失配状态", async () => {
    const pushState = vi.spyOn(window.history, "pushState")
    const user = userEvent.setup()
    render(
      <>
        <NavigationProbe />
        <NavigationProbe />
      </>
    )
    expect(
      screen
        .getAllByLabelText("当前页面")
        .every((page) => page.textContent === "tasks")
    ).toBe(true)

    window.history.replaceState(null, "", "/settings")
    await user.click(screen.getAllByRole("button", { name: "设置" })[0])

    expect(pushState).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe("/settings")
    expect(
      screen
        .getAllByLabelText("当前页面")
        .every((page) => page.textContent === "settings")
    ).toBe(true)
  })

  it("重渲染时保持 navigate 引用稳定", async () => {
    const navigates: Array<(page: AuthenticatedPage) => void> = []
    const user = userEvent.setup()
    render(
      <NavigationProbe onRender={(navigate) => navigates.push(navigate)} />
    )
    await user.click(screen.getByRole("button", { name: "设置" }))
    expect(navigates.length).toBeGreaterThan(1)
    expect(navigates.every((navigate) => navigate === navigates[0])).toBe(true)
  })

  it("卸载一个实例时清理两类 listener 且不再更新该实例", async () => {
    const addEventListener = vi.spyOn(window, "addEventListener")
    const removeEventListener = vi.spyOn(window, "removeEventListener")
    const secondRenders = vi.fn()
    const view = render(
      <>
        <NavigationProbe key="first" />
        <NavigationProbe key="second" onRender={secondRenders} />
      </>
    )
    const addedPopStateListeners = addEventListener.mock.calls
      .filter(([type]) => type === "popstate")
      .map(([, listener]) => listener)
    const addedNavigationListeners = addEventListener.mock.calls
      .filter(([type]) => type === navigationEventName)
      .map(([, listener]) => listener)

    view.rerender(
      <>
        <NavigationProbe key="first" />
      </>
    )
    const removedPopStateListeners = removeEventListener.mock.calls
      .filter(([type]) => type === "popstate")
      .map(([, listener]) => listener)
    const removedNavigationListeners = removeEventListener.mock.calls
      .filter(([type]) => type === navigationEventName)
      .map(([, listener]) => listener)

    expect(removedPopStateListeners).toHaveLength(1)
    expect(addedPopStateListeners).toContain(removedPopStateListeners[0])
    expect(removedNavigationListeners).toHaveLength(1)
    expect(addedNavigationListeners).toContain(removedNavigationListeners[0])

    const renderCountAfterUnmount = secondRenders.mock.calls.length
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "设置" }))
    expect(screen.getByLabelText("当前页面")).toHaveTextContent("settings")
    expect(secondRenders).toHaveBeenCalledTimes(renderCountAfterUnmount)
  })

  it("通过真实 back 和 forward 消费 navigate 创建的历史记录", async () => {
    const user = userEvent.setup()
    render(<NavigationProbe />)
    await user.click(screen.getByRole("button", { name: "设置" }))
    expect(window.location.pathname).toBe("/settings")

    act(() => window.history.back())
    await waitFor(() => {
      expect(window.location.pathname).toBe("/")
      expect(screen.getByLabelText("当前页面")).toHaveTextContent("tasks")
    })

    act(() => window.history.forward())
    await waitFor(() => {
      expect(window.location.pathname).toBe("/settings")
      expect(screen.getByLabelText("当前页面")).toHaveTextContent("settings")
    })

    // jsdom 无法清空 forward 栈；只恢复活动位置，且本用例必须保持为本文件最后一项。
    act(() => window.history.back())
    await waitFor(() => {
      expect(window.location.pathname).toBe("/")
      expect(screen.getByLabelText("当前页面")).toHaveTextContent("tasks")
    })
  })
})
