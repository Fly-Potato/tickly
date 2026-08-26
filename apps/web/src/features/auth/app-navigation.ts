import { useCallback, useEffect, useState } from "react"

export type AuthenticatedPage = "tasks" | "settings"

const navigationEventName = "tickly:navigation"

function pageFromPath(pathname: string): AuthenticatedPage {
  return pathname === "/settings" ? "settings" : "tasks"
}

/** 在两个认证后页面间同步 React 状态和浏览器 History，不引入路由依赖。 */
export function useAppNavigation() {
  const [page, setPage] = useState<AuthenticatedPage>(() =>
    pageFromPath(window.location.pathname)
  )

  useEffect(() => {
    const synchronizeFromLocation = () => {
      if (
        window.location.pathname !== "/" &&
        window.location.pathname !== "/settings"
      ) {
        // History 只能在提交后的 effect 或事件中修正，React render 必须保持纯净。
        window.history.replaceState(null, "", "/")
      }
      setPage(pageFromPath(window.location.pathname))
    }

    synchronizeFromLocation()
    window.addEventListener("popstate", synchronizeFromLocation)
    window.addEventListener(navigationEventName, synchronizeFromLocation)
    return () => {
      window.removeEventListener("popstate", synchronizeFromLocation)
      window.removeEventListener(navigationEventName, synchronizeFromLocation)
    }
  }, [])

  const navigate = useCallback((next: AuthenticatedPage) => {
    const path = next === "settings" ? "/settings" : "/"
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path)
    }
    // pushState 不产生 popstate；同源通知让每个已挂载 hook 都从 location 校准。
    window.dispatchEvent(new Event(navigationEventName))
  }, [])

  return { page, navigate }
}
