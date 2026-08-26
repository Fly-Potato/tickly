import { errorCode, responseError } from "@/lib/api-error"

export type AuthUser = {
  id: string
  username: string
  timezone: string
  is_active: boolean
}

export type TokenResponse = {
  access_token: string
  token_type: "bearer"
  expires_in: number
}

let accessToken: string | null = null
let authenticationGeneration = 0
let refreshOperation: {
  generation: number
  promise: Promise<TokenResponse>
} | null = null
let authenticationFailureHandler: (() => void) | null = null

class StaleAuthenticationOperationError extends Error {
  constructor() {
    super("认证操作已失效")
    this.name = "StaleAuthenticationOperationError"
  }
}

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function invalidateAuthentication() {
  authenticationGeneration += 1
  setAccessToken(null)
}

export function setAuthenticationFailureHandler(handler: (() => void) | null) {
  authenticationFailureHandler = handler
}

export async function login(
  username: string,
  password: string
): Promise<TokenResponse> {
  const generation = authenticationGeneration
  try {
    const token = await requestJson<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    })
    assertCurrentGeneration(generation)
    setAccessToken(token.access_token)
    return token
  } catch (error) {
    if (!isCurrentGeneration(generation)) {
      throw new StaleAuthenticationOperationError()
    }
    throw error
  }
}

export async function refreshAccessToken(): Promise<TokenResponse> {
  const generation = authenticationGeneration
  if (refreshOperation !== null && refreshOperation.generation === generation) {
    return refreshOperation.promise
  }

  const promise = requestJson<TokenResponse>("/api/v1/auth/refresh", {
    method: "POST",
  })
    .then((token) => {
      assertCurrentGeneration(generation)
      setAccessToken(token.access_token)
      return token
    })
    .catch((error: unknown) => {
      if (!isCurrentGeneration(generation)) {
        throw new StaleAuthenticationOperationError()
      }
      notifyAuthenticationFailure(generation)
      throw error
    })
    .finally(() => {
      // 旧代请求可以晚于新代请求结束，只有同一个 Promise 才能清理共享槽位。
      if (refreshOperation?.promise === promise) {
        refreshOperation = null
      }
    })
  refreshOperation = { generation, promise }
  return promise
}

export async function getCurrentUser(): Promise<AuthUser> {
  const response = await apiFetch("/api/v1/auth/me")
  if (!response.ok) {
    throw await responseError(response)
  }
  return (await response.json()) as AuthUser
}

export async function logout(): Promise<void> {
  const generation = authenticationGeneration
  try {
    const response = await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    })
    assertCurrentGeneration(generation)
    if (!response.ok) {
      const error = await responseError(response)
      assertCurrentGeneration(generation)
      throw error
    }
  } catch (error) {
    if (!isCurrentGeneration(generation)) {
      throw new StaleAuthenticationOperationError()
    }
    throw error
  } finally {
    if (isCurrentGeneration(generation)) {
      setAccessToken(null)
    }
  }
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  return authenticatedFetch(input, init, true, authenticationGeneration)
}

async function authenticatedFetch(
  input: RequestInfo | URL,
  init: RequestInit,
  allowRefresh: boolean,
  generation: number
): Promise<Response> {
  assertCurrentGeneration(generation)
  const headers = new Headers(init.headers)
  if (accessToken !== null) {
    headers.set("Authorization", `Bearer ${accessToken}`)
  }
  let response: Response
  try {
    response = await fetch(input, {
      ...init,
      headers,
      credentials: "same-origin",
    })
  } catch (error) {
    assertCurrentGeneration(generation)
    throw error
  }
  assertCurrentGeneration(generation)

  if (
    response.status === 401 &&
    (await errorCode(response)) === "authentication_required"
  ) {
    assertCurrentGeneration(generation)
    if (!allowRefresh) {
      notifyAuthenticationFailure(generation)
      return response
    }
    await refreshAccessToken()
    assertCurrentGeneration(generation)
    return authenticatedFetch(input, init, false, generation)
  }
  return response
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw await responseError(response)
  }
  return (await response.json()) as T
}

function notifyAuthenticationFailure(expectedGeneration: number) {
  if (!isCurrentGeneration(expectedGeneration)) {
    return
  }
  // 当前代认证失败会使所有同代并发操作失效，防止较晚成功的请求重新写入凭据。
  authenticationGeneration += 1
  setAccessToken(null)
  authenticationFailureHandler?.()
}

function isCurrentGeneration(generation: number): boolean {
  return generation === authenticationGeneration
}

function assertCurrentGeneration(generation: number): void {
  if (!isCurrentGeneration(generation)) {
    throw new StaleAuthenticationOperationError()
  }
}
