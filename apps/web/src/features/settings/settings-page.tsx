import type { AuthUser } from "@/features/auth/auth-api"
import { McpTokenSettings } from "./mcp-token-settings"
import { PasswordSettings } from "./password-settings"

export function SettingsPage({ user }: { user: AuthUser }) {
  return (
    <section className="settings-page" aria-labelledby="settings-title">
      <div className="settings-page-heading">
        <div>
          <p className="settings-eyebrow">账号与连接</p>
          <h1 id="settings-title">设置</h1>
          <p>管理登录凭据和连接 Tickly 的 MCP Token。</p>
        </div>
        <dl className="settings-account-summary" aria-label="当前账号">
          <div>
            <dt>用户名</dt>
            <dd>{user.username}</dd>
          </div>
          <div>
            <dt>时区</dt>
            <dd>{user.timezone}</dd>
          </div>
        </dl>
      </div>

      <div className="settings-tools">
        <PasswordSettings />
        <McpTokenSettings timeZone={user.timezone} />
      </div>
    </section>
  )
}
