/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"

import {
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  refreshAccessToken,
  setAccessToken,
  setAuthenticationFailureHandler,
  type AuthUser,
} from "./auth-api"

export type AuthState =
  | { status: "initializing" }
  | { status: "anonymous"; error?: string }
  | { status: "authenticated"; user: AuthUser }

type AuthContextValue = {
  state: AuthState
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "initializing" })

  useEffect(() => {
    let active = true
    setAuthenticationFailureHandler(() => {
      if (active) {
        setState({ status: "anonymous" })
      }
    })

    async function restoreSession() {
      try {
        await refreshAccessToken()
        const user = await getCurrentUser()
        if (active) {
          setState({ status: "authenticated", user })
        }
      } catch {
        setAccessToken(null)
        if (active) {
          setState({ status: "anonymous" })
        }
      }
    }

    void restoreSession()
    return () => {
      active = false
      setAuthenticationFailureHandler(null)
    }
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      async login(username, password) {
        try {
          await loginRequest(username, password)
          const user = await getCurrentUser()
          setState({ status: "authenticated", user })
        } catch (error) {
          setAccessToken(null)
          setState({ status: "anonymous", error: "用户名或密码错误" })
          throw error
        }
      },
      async logout() {
        try {
          await logoutRequest()
        } catch {
          // 网络失败不能阻止本地清除内存 token；服务端会话仍受绝对期限约束。
        } finally {
          setAccessToken(null)
          setState({ status: "anonymous" })
        }
      },
    }),
    [state],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error("useAuth 必须在 AuthProvider 内使用")
  }
  return context
}
