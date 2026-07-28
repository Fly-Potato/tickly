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

type ErrorEnvelope = {
  error?: {
    code?: string
    message?: string
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(
    status: number,
    code: string,
    message: string,
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

let accessToken: string | null = null
let refreshPromise: Promise<TokenResponse> | null = null
let authenticationFailureHandler: (() => void) | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function setAuthenticationFailureHandler(
  handler: (() => void) | null,
) {
  authenticationFailureHandler = handler
}

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const token = await requestJson<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
  setAccessToken(token.access_token)
  return token
}

export async function refreshAccessToken(): Promise<TokenResponse> {
  if (refreshPromise === null) {
    refreshPromise = requestJson<TokenResponse>("/api/v1/auth/refresh", {
      method: "POST",
    })
      .then((token) => {
        setAccessToken(token.access_token)
        return token
      })
      .catch((error: unknown) => {
        notifyAuthenticationFailure()
        throw error
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

export async function getCurrentUser(): Promise<AuthUser> {
  const response = await apiFetch("/api/v1/auth/me")
  if (!response.ok) {
    throw await responseError(response)
  }
  return (await response.json()) as AuthUser
}

export async function logout(): Promise<void> {
  try {
    const response = await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    })
    if (!response.ok) {
      throw await responseError(response)
    }
  } finally {
    setAccessToken(null)
  }
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  return authenticatedFetch(input, init, true)
}

async function authenticatedFetch(
  input: RequestInfo | URL,
  init: RequestInit,
  allowRefresh: boolean,
): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken !== null) {
    headers.set("Authorization", `Bearer ${accessToken}`)
  }
  const response = await fetch(input, {
    ...init,
    headers,
    credentials: "same-origin",
  })

  if (
    response.status === 401 &&
    (await errorCode(response)) === "authentication_required"
  ) {
    if (!allowRefresh) {
      notifyAuthenticationFailure()
      return response
    }
    await refreshAccessToken()
    return authenticatedFetch(input, init, false)
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

async function errorCode(response: Response): Promise<string | undefined> {
  try {
    const payload = (await response.clone().json()) as ErrorEnvelope
    return payload.error?.code
  } catch {
    return undefined
  }
}

async function responseError(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.clone().json()) as ErrorEnvelope
    return new ApiError(
      response.status,
      payload.error?.code ?? "request_failed",
      payload.error?.message ?? "请求失败",
    )
  } catch {
    return new ApiError(response.status, "request_failed", "请求失败")
  }
}

function notifyAuthenticationFailure() {
  setAccessToken(null)
  authenticationFailureHandler?.()
}
