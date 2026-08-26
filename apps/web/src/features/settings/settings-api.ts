import { apiFetch } from "@/features/auth/auth-api"
import { responseError } from "@/lib/api-error"

export type McpTokenStatus = "active" | "expired" | "revoked"
export type McpTokenExpiryDays = 90 | 365 | null

export type McpToken = {
  id: string
  name: string
  status: McpTokenStatus
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

export type IssuedMcpToken = {
  item: McpToken
  token: string
}

async function settingsRequest<T>(
  url: string,
  init: RequestInit = {}
): Promise<T> {
  const response = await apiFetch(url, init)
  if (!response.ok) {
    throw await responseError(response)
  }
  return (await response.json()) as T
}

export async function changePassword(
  currentPassword: string,
  newPassword: string
): Promise<void> {
  const response = await apiFetch("/api/v1/account/password", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
  if (!response.ok) {
    throw await responseError(response)
  }
}

export async function listMcpTokens(): Promise<McpToken[]> {
  const response = await settingsRequest<{ items: McpToken[] }>(
    "/api/v1/mcp-tokens"
  )
  return response.items
}

export function createMcpToken(
  name: string,
  expiresInDays: McpTokenExpiryDays
): Promise<IssuedMcpToken> {
  return settingsRequest("/api/v1/mcp-tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, expires_in_days: expiresInDays }),
  })
}

export async function revokeMcpToken(tokenId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/mcp-tokens/${encodeURIComponent(tokenId)}`,
    { method: "DELETE" }
  )
  if (!response.ok) {
    throw await responseError(response)
  }
}
