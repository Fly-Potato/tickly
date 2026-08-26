import { useState } from "react"

import { TodoWorkspace } from "@/features/tasks/todo-workspace"
import { SettingsPage } from "@/features/settings/settings-page"
import { useAppNavigation } from "./app-navigation"
import { useAuth } from "./auth-context"
import { AuthenticatedHeader } from "./authenticated-header"

export function AuthenticatedShell() {
  const { state, logout } = useAuth()
  const [loggingOut, setLoggingOut] = useState(false)
  const { page, navigate } = useAppNavigation()

  if (state.status !== "authenticated") {
    return null
  }

  async function handleLogout() {
    setLoggingOut(true)
    await logout()
  }

  return (
    <main className="todo-page">
      <section className="todo-shell">
        <AuthenticatedHeader
          page={page}
          username={state.user.username}
          timeZone={state.user.timezone}
          loggingOut={loggingOut}
          onNavigate={navigate}
          onLogout={handleLogout}
        />
        {page === "settings" ? (
          <SettingsPage user={state.user} />
        ) : (
          <TodoWorkspace timeZone={state.user.timezone} />
        )}
      </section>
    </main>
  )
}
