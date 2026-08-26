import { KeyRound } from "lucide-react"
import { useState, type FormEvent } from "react"
import { flushSync } from "react-dom"

import { Button } from "@/components/ui/button"
import { useAuth } from "@/features/auth/auth-context"
import { safeErrorMessage } from "@/lib/api-error"
import { changePassword } from "./settings-api"

const MIN_PASSWORD_LENGTH = 6

export function PasswordSettings() {
  const { completePasswordChange } = useAuth()
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmation, setConfirmation] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) {
      return
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致")
      return
    }
    // 这里只镜像服务端公开的最小长度，其他密码规则仍由 API 统一判定。
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      await changePassword(currentPassword, newPassword)
      // 完成回调会卸载整个认证树，必须先同步清除 DOM 中仍挂载的敏感输入值。
      flushSync(() => {
        setCurrentPassword("")
        setNewPassword("")
        setConfirmation("")
      })
      completePasswordChange()
    } catch (caught) {
      setError(safeErrorMessage(caught, "密码修改失败"))
    } finally {
      setSubmitting(false)
    }
  }

  function clearError() {
    if (error !== null) {
      setError(null)
    }
  }

  return (
    <section
      className="settings-tool-card"
      aria-labelledby="password-settings-title"
    >
      <div className="settings-card-heading">
        <span className="settings-card-icon" aria-hidden="true">
          <KeyRound />
        </span>
        <div>
          <h2 id="password-settings-title">修改密码</h2>
          <p>更新后，当前浏览器和其他已登录设备都需要重新登录。</p>
        </div>
      </div>

      <form className="settings-password-form" onSubmit={handleSubmit}>
        <div className="settings-field">
          <label htmlFor="settings-current-password">当前密码</label>
          <input
            id="settings-current-password"
            name="current-password"
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => {
              setCurrentPassword(event.target.value)
              clearError()
            }}
            required
            disabled={submitting}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="settings-new-password">新密码</label>
          <input
            id="settings-new-password"
            name="new-password"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => {
              setNewPassword(event.target.value)
              clearError()
            }}
            minLength={MIN_PASSWORD_LENGTH}
            required
            disabled={submitting}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="settings-confirm-password">确认新密码</label>
          <input
            id="settings-confirm-password"
            name="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmation}
            onChange={(event) => {
              setConfirmation(event.target.value)
              clearError()
            }}
            minLength={MIN_PASSWORD_LENGTH}
            required
            disabled={submitting}
          />
        </div>

        {error ? (
          <p role="alert" className="settings-inline-error">
            {error}
          </p>
        ) : null}

        <div className="settings-card-actions">
          <Button type="submit" disabled={submitting}>
            <KeyRound aria-hidden="true" />
            {submitting ? "正在修改" : "修改密码"}
          </Button>
        </div>
      </form>
    </section>
  )
}
