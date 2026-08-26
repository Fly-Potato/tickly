import { ArrowLeft, LogOut, Settings as SettingsIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { AuthenticatedPage } from "./app-navigation"

type AuthenticatedHeaderProps = {
  page: AuthenticatedPage
  username: string
  timeZone: string
  loggingOut: boolean
  onNavigate(page: AuthenticatedPage): void
  onLogout(): Promise<void>
}

/** 认证后页面共用的纯展示 Header；导航和退出状态由外壳统一持有。 */
export function AuthenticatedHeader({
  page,
  username,
  timeZone,
  loggingOut,
  onNavigate,
  onLogout,
}: AuthenticatedHeaderProps) {
  const navigationTarget = page === "tasks" ? "settings" : "tasks"

  return (
    <header className="todo-header">
      <button
        type="button"
        className="todo-brand-button auth-brand-row"
        aria-label="Tickly，返回待办"
        onClick={() => onNavigate("tasks")}
      >
        <span className="auth-brand-mark" aria-hidden="true">
          T
        </span>
        <span className="text-left">
          <span className="auth-brand-name block">Tickly</span>
          <span className="todo-brand-note block">Personal cadence</span>
        </span>
      </button>
      <div className="todo-header-actions">
        <div className="todo-account">
          <strong>{username}</strong>
          <span>{timeZone}</span>
        </div>
        <div className="todo-header-buttons">
          <Button
            type="button"
            variant="outline"
            onClick={() => onNavigate(navigationTarget)}
          >
            {page === "tasks" ? (
              <SettingsIcon aria-hidden="true" />
            ) : (
              <ArrowLeft aria-hidden="true" />
            )}
            {page === "tasks" ? "设置" : "返回待办"}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={loggingOut}
            onClick={() => void onLogout()}
          >
            <LogOut aria-hidden="true" />
            {loggingOut ? "正在退出" : "退出登录"}
          </Button>
        </div>
      </div>
    </header>
  )
}
