import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { useAuth } from "./auth-context"

const inputClassName =
  "h-12 w-full rounded-xl border border-input bg-background/90 px-4 text-base shadow-sm transition-colors placeholder:text-muted-foreground/70 focus-visible:border-primary focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-60"

export function LoginForm() {
  const { state, login } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) {
      return
    }

    setSubmitting(true)
    setLocalError(null)
    try {
      await login(username, password)
    } catch {
      setLocalError("用户名或密码错误")
    } finally {
      setSubmitting(false)
    }
  }

  const error = localError ?? (state.status === "anonymous" ? state.error : null)

  return (
    <main className="auth-page">
      <section className="auth-layout" aria-labelledby="login-title">
        <div className="auth-intro">
          <div className="auth-brand-row">
            <span className="auth-brand-mark">T</span>
            <span className="auth-brand-name">Tickly</span>
          </div>
          <div className="auth-dial" aria-hidden="true">
            <span />
          </div>
          <p className="auth-kicker">个人工作空间</p>
          <p className="auth-statement">让每一天，都有清晰的落点。</p>
        </div>

        <div className="auth-card">
          <div>
            <p className="auth-card-index">01 / 身份验证</p>
            <h1 id="login-title" className="auth-title">
              登录 Tickly
            </h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              使用由管理员创建的用户名继续。
            </p>
          </div>

          <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="username">
                用户名
              </label>
              <input
                id="username"
                name="username"
                className={inputClassName}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                disabled={submitting}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="password">
                密码
              </label>
              <input
                id="password"
                name="password"
                type="password"
                className={inputClassName}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
                disabled={submitting}
              />
            </div>

            {error ? (
              <p
                role="alert"
                className="rounded-xl border border-destructive/20 bg-destructive/8 px-4 py-3 text-sm text-destructive"
              >
                {error}
              </p>
            ) : null}

            <Button
              type="submit"
              className="h-12 w-full rounded-xl text-sm font-semibold"
              disabled={submitting}
            >
              {submitting ? "正在登录" : "登录"}
            </Button>
          </form>
        </div>
      </section>
    </main>
  )
}
