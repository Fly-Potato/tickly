import { useState } from "react"

import { Button } from "@/components/ui/button"
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
    <main className="auth-page">
      <section className="auth-ready-card" aria-labelledby="ready-title">
        <div className="auth-brand-row">
          <span className="auth-brand-mark">T</span>
          <span className="auth-brand-name">Tickly</span>
        </div>
        <p className="auth-card-index mt-12">当前账号</p>
        <h1 id="ready-title" className="mt-3 text-4xl font-semibold tracking-tight">
          {state.user.username}
        </h1>
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span className="size-2 rounded-full bg-emerald-500 shadow-[0_0_0_5px_color-mix(in_oklab,var(--color-emerald-500)_14%,transparent)]" />
          <span>认证已就绪</span>
        </div>
        <Button
          type="button"
          variant="outline"
          className="mt-12 rounded-xl"
          disabled={loggingOut}
          onClick={handleLogout}
        >
          {loggingOut ? "正在退出" : "退出登录"}
        </Button>
      </section>
    </main>
  )
}
