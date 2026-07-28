type ErrorEnvelope = {
  error?: {
    code?: string
    message?: string
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

export async function errorCode(response: Response): Promise<string | undefined> {
  try {
    const payload = (await response.clone().json()) as ErrorEnvelope
    return payload.error?.code
  } catch {
    return undefined
  }
}

export async function responseError(response: Response): Promise<ApiError> {
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

export function safeErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}
