import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { App } from "@/App"
import { AuthProvider } from "@/features/auth/auth-context"

describe("Web 测试环境", () => {
  it("可以在 jsdom 中进入用户名登录页", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: "refresh_required" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )
    render(
      <AuthProvider>
        <App />
      </AuthProvider>,
    )

    expect(
      await screen.findByRole("heading", { name: "登录 Tickly" }),
    ).toBeInTheDocument()
  })
})
