/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"

import {
  getCurrentUser,
  invalidateAuthentication,
  login as loginRequest,
  logout as logoutRequest,
  refreshAccessToken,
  setAuthenticationFailureHandler,
  type AuthUser,
} from "./auth-api"

export type AuthState =
  | { status: "initializing" }
  | { status: "anonymous"; error?: string; notice?: string }
  | { status: "authenticated"; user: AuthUser }

type AuthContextValue = {
  state: AuthState
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
  completePasswordChange(): void
}

const AuthContext = createContext<AuthContextValue | null>(null)
let authenticationLifecycleNonce = 0

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "initializing" })
  const operationGenerationRef = useRef(0)
  const acceptsAuthenticationFailureRef = useRef(false)

  useEffect(() => {
    let active = true
    authenticationLifecycleNonce += 1
    const restoreGeneration = ++operationGenerationRef.current
    acceptsAuthenticationFailureRef.current = true
    setAuthenticationFailureHandler(() => {
      if (active && acceptsAuthenticationFailureRef.current) {
        acceptsAuthenticationFailureRef.current = false
        operationGenerationRef.current += 1
        setState({ status: "anonymous" })
      }
    })

    async function restoreSession() {
      try {
        await refreshAccessToken()
        if (!active || operationGenerationRef.current !== restoreGeneration) {
          return
        }
        const user = await getCurrentUser()
        if (active && operationGenerationRef.current === restoreGeneration) {
          setState({ status: "authenticated", user })
        }
      } catch {
        if (active && operationGenerationRef.current === restoreGeneration) {
          acceptsAuthenticationFailureRef.current = false
          invalidateAuthentication()
          setState({ status: "anonymous" })
        }
      }
    }

    void restoreSession()
    return () => {
      active = false
      acceptsAuthenticationFailureRef.current = false
      operationGenerationRef.current += 1
      setAuthenticationFailureHandler(null)
      const cleanupNonce = ++authenticationLifecycleNonce
      queueMicrotask(() => {
        // StrictMode 会同步重新 setup；只有同一 tick 内未被接管才是真正卸载。
        if (authenticationLifecycleNonce === cleanupNonce) {
          invalidateAuthentication()
        }
      })
    }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const operationGeneration = ++operationGenerationRef.current
    invalidateAuthentication()
    acceptsAuthenticationFailureRef.current = true
    // 新登录尝试开始时立即丢弃上一轮提示，避免异步请求期间共享状态仍暴露过期消息。
    setState({ status: "anonymous" })
    try {
      await loginRequest(username, password)
      if (operationGenerationRef.current !== operationGeneration) {
        return
      }
      const user = await getCurrentUser()
      if (operationGenerationRef.current === operationGeneration) {
        setState({ status: "authenticated", user })
      }
    } catch (error) {
      if (operationGenerationRef.current !== operationGeneration) {
        return
      }
      acceptsAuthenticationFailureRef.current = false
      invalidateAuthentication()
      setState({ status: "anonymous", error: "用户名或密码错误" })
      throw error
    }
  }, [])

  const logout = useCallback(async () => {
    const operationGeneration = ++operationGenerationRef.current
    acceptsAuthenticationFailureRef.current = false
    invalidateAuthentication()
    try {
      await logoutRequest()
    } catch {
      // 网络失败不能阻止本地清除内存 token；服务端会话仍受绝对期限约束。
    } finally {
      if (operationGenerationRef.current === operationGeneration) {
        setState({ status: "anonymous" })
      }
    }
  }, [])

  const completePasswordChange = useCallback(() => {
    // 服务端已撤销 refresh session；这里只清除浏览器内存态，不额外发送登出请求。
    acceptsAuthenticationFailureRef.current = false
    operationGenerationRef.current += 1
    invalidateAuthentication()
    setState({ status: "anonymous", notice: "密码已修改，请重新登录" })
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ state, login, logout, completePasswordChange }),
    [completePasswordChange, login, logout, state]
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
