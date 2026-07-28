import { AuthenticatedShell } from "@/features/auth/authenticated-shell"
import { useAuth } from "@/features/auth/auth-context"
import { LoginForm } from "@/features/auth/login-form"

export function App() {
  const { state } = useAuth()

  if (state.status === "initializing") {
    return (
      <main className="auth-page grid place-items-center">
        <div role="status" className="text-center">
          <div className="auth-loading-mark" aria-hidden="true" />
          <p className="mt-5 text-sm text-muted-foreground">正在恢复登录状态</p>
        </div>
      </main>
    )
  }

  return state.status === "authenticated" ? <AuthenticatedShell /> : <LoginForm />
}

export default App
