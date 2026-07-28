import { spawn } from "node:child_process"
import process from "node:process"

const projectName = `tickly-smoke-${process.pid}`
const repositoryRoot = process.cwd()

function run(command, args, { capture = false, allowFailure = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: repositoryRoot,
      shell: false,
      stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
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
  await compose(["up", "--detach"])

  const apiContainer = await waitForHealthy("api")
  const webContainer = await waitForHealthy("web")

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
