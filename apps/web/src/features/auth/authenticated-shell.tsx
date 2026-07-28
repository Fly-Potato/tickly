import { useState } from "react"

import { TodoWorkspace } from "@/features/tasks/todo-workspace"
import { useAuth } from "./auth-context"

export function AuthenticatedShell() {
  const { state, logout } = useAuth()
  const [loggingOut, setLoggingOut] = useState(false)

  if (state.status !== "authenticated") {
    return null
  }

  async function handleLogout() {
    setLoggingOut(true)
    await logout()
  }

  return (
    <TodoWorkspace
      username={state.user.username}
      timeZone={state.user.timezone}
      loggingOut={loggingOut}
      onLogout={handleLogout}
    />
  )
}
