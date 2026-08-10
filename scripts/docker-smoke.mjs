import { spawn } from "node:child_process"
import { randomBytes } from "node:crypto"
import process from "node:process"

const projectName = `tickly-smoke-${process.pid}`
const repositoryRoot = process.cwd()
const smokeUsername = "smoke-user"
const smokePassword = randomBytes(24).toString("base64url")
const apiPort = "18080"

// 只向本次 smoke 的 Compose 子进程注入一次性生产配置，不写入文件或日志。
process.env.TICKLY_JWT_SECRET = randomBytes(32).toString("hex")
process.env.TICKLY_REFRESH_COOKIE_SECURE = "true"
// 使用非默认端口验证 API、健康检查和 Caddy 上游共享同一份配置。
process.env.TICKLY_PORT = apiPort

function run(
  command,
  args,
  { capture = false, allowFailure = false, input } = {},
) {
  return new Promise((resolve, reject) => {
    const stdin = input === undefined ? (capture ? "ignore" : "inherit") : "pipe"
    const child = spawn(command, args, {
      cwd: repositoryRoot,
      shell: false,
      stdio: [stdin, capture ? "pipe" : "inherit", capture ? "pipe" : "inherit"],
    })
    let stdout = ""
    let stderr = ""

    if (capture) {
      child.stdout.setEncoding("utf8")
      child.stderr.setEncoding("utf8")
      child.stdout.on("data", (chunk) => {
        stdout += chunk
      })
      child.stderr.on("data", (chunk) => {
        stderr += chunk
      })
    }

    if (input !== undefined) {
      child.stdin.end(input)
    }

    child.on("error", reject)
    child.on("close", (code) => {
      const result = { code: code ?? 1, stdout, stderr }
      if (result.code === 0 || allowFailure) {
        resolve(result)
        return
      }

      reject(
        new Error(
          `${command} ${args.join(" ")} 退出码为 ${result.code}${stderr ? `\n${stderr.trim()}` : ""}`,
        ),
      )
    })
  })
}

function compose(args, options) {
  return run(
    "docker",
    ["compose", "--project-name", projectName, ...args],
    options,
  )
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function containerId(service) {
  const result = await compose(["ps", "--quiet", service], { capture: true })
  return result.stdout.trim()
}

async function waitForHealthy(service) {
  for (let remaining = 60; remaining > 0; remaining -= 1) {
    const id = await containerId(service)
    if (id) {
      const result = await run(
        "docker",
        [
          "inspect",
          "--format",
          "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
          id,
        ],
        { capture: true },
      )
      const health = result.stdout.trim()
      if (health === "healthy") {
        return id
      }
      if (health === "unhealthy") {
        throw new Error(`${service} 容器 healthcheck 失败`)
      }
    }
    await wait(1000)
  }

  throw new Error(`${service} 容器未在 60 秒内进入 healthy 状态`)
}

async function requestText(url) {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`${url} 返回 HTTP ${response.status}`)
  }
  return response.text()
}

async function loginThroughWeb() {
  const response = await fetch("http://127.0.0.1:8080/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: smokeUsername,
      password: smokePassword,
    }),
  })
  if (!response.ok) {
    throw new Error(`登录接口返回 HTTP ${response.status}`)
  }

  const cookie = response.headers.get("set-cookie") ?? ""
  const normalizedCookie = cookie.toLowerCase()
  for (const attribute of [
    "tickly_refresh=",
    "httponly",
    "secure",
    "samesite=strict",
    "path=/api/v1/auth",
  ]) {
    if (!normalizedCookie.includes(attribute)) {
      throw new Error(`refresh Cookie 缺少属性：${attribute}`)
    }
  }

  const payload = await response.json()
  if (payload.token_type !== "bearer" || typeof payload.access_token !== "string") {
    throw new Error("登录响应未返回有效 access token")
  }
  return payload.access_token
}

async function assertCurrentUser(accessToken) {
  const response = await fetch("http://127.0.0.1:8080/api/v1/auth/me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!response.ok) {
    throw new Error(`/auth/me 返回 HTTP ${response.status}`)
  }
  const payload = await response.json()
  if (payload.username !== smokeUsername) {
    throw new Error("/auth/me 未返回 smoke 账号")
  }
}

async function assertNonRoot(container, service) {
  const result = await run(
    "docker",
    ["inspect", "--format", "{{.Config.User}}", container],
    { capture: true },
  )
  const user = result.stdout.trim()
  if (!user || user === "root" || user === "0") {
    throw new Error(`${service} 容器必须使用非 root 用户运行`)
  }
}

async function assertContainerEnvironment(container, name, expectedValue) {
  const result = await run(
    "docker",
    ["inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container],
    { capture: true },
  )
  const entries = result.stdout.split("\n")
  if (!entries.includes(`${name}=${expectedValue}`)) {
    throw new Error(`API 容器未使用 ${name}=${expectedValue}`)
  }
}

let cleanupPromise

function cleanup() {
  // smoke 使用独立 project name；清理只触及本次创建的容器、网络和测试 volume。
  cleanupPromise ??= compose(["down", "--volumes", "--remove-orphans"], {
    capture: true,
    allowFailure: true,
  })
  return cleanupPromise
}

async function main() {
  await compose(["config", "--quiet"])
  await compose(["build"])
  // migration 必须是独立步骤；API 启动命令不得隐式修改生产 schema。
  await compose(["run", "--rm", "api", "alembic", "upgrade", "head"])
  // 密码只经 stdin 进入 getpass，不出现在 argv、Compose 环境或命令日志中。
  await compose(
    [
      "run",
      "--rm",
      "-T",
      "api",
      "python",
      "-m",
      "app.cli",
      "user",
      "create",
      "--username",
      smokeUsername,
    ],
    { input: `${smokePassword}\n${smokePassword}\n` },
  )
  await compose(["up", "--detach"])

  const apiContainer = await waitForHealthy("api")
  const webContainer = await waitForHealthy("web")
  await assertContainerEnvironment(apiContainer, "TICKLY_PORT", apiPort)

  const html = await requestText("http://127.0.0.1:8080/")
  if (!html.includes('id="root"')) {
    throw new Error("Web 首页未返回 React 根节点")
  }
  if ((await requestText("http://127.0.0.1:8080/health")) !== '{"status":"ok"}') {
    throw new Error("/health 响应不符合预期")
  }
  if ((await requestText("http://127.0.0.1:8080/ready")) !== '{"status":"ready"}') {
    throw new Error("/ready 响应不符合预期")
  }
  const accessToken = await loginThroughWeb()
  await assertCurrentUser(accessToken)

  const publishedApiPorts = await run("docker", ["port", apiContainer], {
    capture: true,
  })
  if (publishedApiPorts.stdout.trim()) {
    throw new Error("API 端口不应发布到宿主机")
  }

  await assertNonRoot(apiContainer, "API")
  await assertNonRoot(webContainer, "Web")
  console.log("Docker smoke test passed")
}

for (const [signal, exitCode] of [
  ["SIGINT", 130],
  ["SIGTERM", 143],
]) {
  process.once(signal, () => {
    cleanup().finally(() => process.exit(exitCode))
  })
}

try {
  await main()
} catch (error) {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
} finally {
  await cleanup()
}
