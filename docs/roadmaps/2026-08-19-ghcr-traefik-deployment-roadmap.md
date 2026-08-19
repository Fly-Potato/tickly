# Tickly GHCR 公开镜像与 Traefik 部署路线图

## 推进记录

2026-08-19 已完成阶段 1–3 的本地实现：GHCR 发布 job、Traefik Compose 覆盖、自动检查与部署文档均已写入工作区；全仓检查、GitHub Actions YAML 语法、基础 Compose 与合并 Compose 静态解析已经通过。Docker daemon 启动后，API、MCP、Web 三套镜像均构建成功，镜像默认用户分别为 `tickly`、`tickly-mcp`、`caddy`；独立 `tickly-validation` Compose 项目完成 migration，三个服务均 healthy，经 Caddy 的 `/health`、`/ready`、`/internal/*` 与无 Token `/mcp` smoke 符合预期，随后已删除本轮临时容器、网络和测试 volume。由于本机没有 `pwsh`，扩展后的 PowerShell 检查脚本尚未动态执行。沙箱外 GitHub CLI 已补充 `read:packages`，并确认 `Fly-Potato` 当前没有 Container Package，三个目标包名不存在冲突。代码尚未提交或推送，因此 Actions、镜像发布、Package Public、匿名拉取和真实 Traefik HTTPS 均未发生。

## 目标

在不改变 Tickly 现有 Web/API/MCP 业务契约的前提下，建立一条可重复、可追溯的容器发布与单 VPS 部署链路：

- GitHub Actions 必须先通过现有全仓检查，再构建并发布 API、MCP、Web 三个镜像。
- 镜像发布到 GitHub Container Registry（GHCR），并在首次发布验证后明确设为公开。
- 线上通过一个小型 Compose 覆盖文件拉取预构建镜像，不在 VPS 上重新构建源码。
- Traefik 负责公网域名、HTTPS 和证书；Web/Caddy 继续负责静态文件、同源 API/MCP 转发与内部路径阻断。
- 发布、公开可见性、部署和运行时 smoke 分别验收，不把其中任一步的成功描述为整条链路已经完成。

## 已确认决策

- 镜像仓库使用 `ghcr.io/fly-potato` 命名空间。
- 发布三个独立镜像：
  - `ghcr.io/fly-potato/tickly-api`
  - `ghcr.io/fly-potato/tickly-mcp`
  - `ghcr.io/fly-potato/tickly-web`
- 镜像面向公开拉取；首次改为 Public 后不可恢复为 Private，公开操作属于独立且需要明确验收的发布步骤。
- 发布任务接在现有 CI 全仓检查之后，不为镜像发布建立一条绕过测试的平行路径。
- 镜像同时发布 `linux/amd64` 和 `linux/arm64` manifest。
- `main` 发布 `latest` 和不可重复的 `sha-*` 标签；`v*` tag 发布版本标签和 `sha-*` 标签。
- 生产部署必须使用明确的 `v*` 或 `sha-*` 标签，不使用会漂移的 `latest`。
- 保留基础 `compose.yaml` 用于当前本地构建和生产形态 smoke；新增 `compose.traefik.yaml` 只表达线上差异。
- Traefik 只连接 Web/Caddy，不直接连接 API 或 MCP。
- `/api/*`、`/mcp` 和 `/internal/*` 的现有路径语义保持不变，不使用 `StripPrefix`。
- 提交、推送、首次 Actions 发布、将包设为 Public 是四个独立边界；没有实际完成的步骤不得描述为已发布。

## 当前基线

截至 2026-08-19，仓库已经具备：

- `.github/workflows/ci.yml`：PR 与 `main` push 时运行全仓检查，第三方 Actions 固定到 commit SHA。
- `apps/api/Dockerfile`：构建 API 生产镜像，运行身份为非 root，包含 Alembic migration 文件。
- `apps/mcp/Dockerfile`：构建 MCP 生产镜像，运行身份为独立非 root 用户，不直接持有 SQLite。
- `apps/web/Dockerfile`：构建 React 静态资源，并由非 root Caddy 在 `8080` 提供服务。
- `apps/web/Caddyfile`：依次阻断 `/internal/*`、转发 `/mcp`、转发 `/api/*`，最后进入 SPA fallback。
- `compose.yaml`：本地构建三个镜像，仅将 Web/Caddy 的 `8080` 发布到宿主机；API 和 MCP 只在 Compose 网络暴露。
- `.dockerignore`：排除 `.env`、私钥、数据库、虚拟环境、缓存、测试和文档等不应进入镜像的内容。
- `scripts/check-compose.ps1`：检查 Compose 服务边界、共享 MCP Token 摘要、依赖顺序、Caddy 路由顺序和非 root 文件所有权约束。

当前尚未具备：

- GHCR 镜像发布 job。
- `main`/版本 tag 到容器标签的稳定映射。
- 镜像构建 provenance attestation。
- Traefik Compose 覆盖文件与对应静态检查。
- 三个 GHCR Package 的 Public 可见性与匿名拉取验证。
- 真实 Traefik HTTPS 入口下的 Web/API/MCP smoke 记录。

实施前置状态需要重新核对：

- 2026-08-19 后续在沙箱外为 `gh` 补充 `read:packages` 后，Package 列表请求成功并返回空列表；`tickly-api`、`tickly-mcp`、`tickly-web` 当前均不存在名称冲突。`Fly-Potato/tickly` 已确认为 Public 仓库，默认分支为 `main`，GitHub Actions 已启用且允许全部 Actions。
- 2026-08-19 后续已启动 Docker daemon，三镜像构建与基础 Compose 容器 smoke 通过；真实多架构 Actions 构建和 Traefik HTTPS smoke 仍需远端验证。
- Traefik 现有外部网络名、HTTPS entrypoint 名和证书 resolver 名尚未写死到仓库，必须通过部署变量注入。

## 目标架构

```text
pull request
  └─ CI 全仓检查

main / v* tag
  └─ CI 全仓检查
       └─ matrix 构建与推送
            ├─ ghcr.io/fly-potato/tickly-api
            ├─ ghcr.io/fly-potato/tickly-mcp
            └─ ghcr.io/fly-potato/tickly-web

single VPS
  └─ Traefik（公网 80/443、TLS、证书）
       └─ Web/Caddy :8080
            ├─ /internal/* → 404
            ├─ /mcp        → MCP :8322
            ├─ /api/*      → API :8321
            └─ 其他路径     → React SPA
```

安全边界：

- Traefik 外部网络只有 Web 服务加入。
- API 与 MCP 不发布宿主机端口，也不加入 Traefik 外部网络。
- Caddy 保留 `/internal/*` 固定 `404`，避免内部 MCP API 落入 SPA 或被公网转发。
- MCP 继续使用 Bearer Token 摘要、Host 白名单和 Origin 白名单；公开镜像不包含原始 Token 或生产 `.env`。
- API 继续独占 SQLite volume 和 migration；镜像公开不改变数据与运行配置的私密性。

## 镜像与标签契约

| 触发来源 | 生成标签 | 用途 |
| --- | --- | --- |
| `main` push | `latest`、`sha-<短提交>` | `latest` 仅便于查看当前主线；`sha-*` 用于精确部署与回滚 |
| `v1.2.3` tag | `v1.2.3`、`1.2.3`、`sha-<短提交>` | 正式版本发布与生产部署 |
| pull request | 不发布 | 只执行现有全仓检查 |

不变量：

- 同一次 workflow 的三个镜像使用同一组版本语义；生产部署前必须确认同一标签在三套镜像中都存在并对应同一源提交。
- `sha-*` 标签不得复用到其他提交。
- 正式 `v*` 标签不得覆盖已经发布的不同 digest；需要修复时发布新 patch 版本。
- 每个镜像带有 source、revision、version、created 等 OCI 元数据，并关联 `Fly-Potato/tickly`。
- 每个镜像发布 manifest digest 和 GitHub artifact attestation。

## 分阶段路线图

### 阶段 0：发布前置检查

目标：在写入 GitHub Packages 前消除命名、权限和本地验证阻塞。

范围：

- 修复或重新建立本机 GitHub CLI 登录，但不打印或持久化临时令牌。
- 查询 `tickly-api`、`tickly-mcp`、`tickly-web` 是否已存在。
- 若存在同名包，确认其所有者、仓库关联、Actions access 和当前可见性，禁止直接覆盖未关联的包。
- 启动 Docker daemon，确认当前 Docker/Buildx 可以构建三个 Dockerfile。
- 确认目标 VPS 架构；默认仍发布 amd64/arm64 两种平台。
- 收集实际 Traefik 网络、entrypoint、certificate resolver 和域名名称。

验收：

- `gh auth status` 成功且不会输出 token。
- 三个包名不存在冲突，或已确认由 `Fly-Potato/tickly` 管理。
- `docker info` 成功。
- Traefik 所需四项部署变量都有明确值。

### 阶段 1：在现有 CI 后发布三个镜像

目标：只有全仓检查通过的主线或版本提交可以进入 GHCR。

范围：

- 扩展 `.github/workflows/ci.yml` 的 push 触发，增加 `v*` tag。
- 新增依赖现有 `check` job 的镜像发布 job；PR 条件下不运行发布。
- 为发布 job 设置最小权限：`contents: read`、`packages: write`、`attestations: write`、`id-token: write`。
- 通过 matrix 定义三个镜像名称和 Dockerfile。
- 使用固定 commit SHA 的 QEMU、Buildx、GHCR login、metadata、build-push 和 attestation Actions。
- 使用 `GITHUB_TOKEN` 登录 GHCR，不新增长期 Registry PAT secret。
- 为每个镜像使用独立 GitHub Actions build cache scope。
- 构建并发布 `linux/amd64,linux/arm64`。

验收：

- PR 只出现全仓检查，不出现可写 Packages 的 job。
- `main` 或 `v*` 必须等待 `check` 成功后才开始发布。
- 任一镜像构建失败时 workflow 整体失败，该标签组不得进入部署；其他已成功推送的同组镜像可能已经存在，重试后仍需重新核对三套 digest。
- 三个包都能定位到源仓库和对应 commit。
- 三个镜像都存在 manifest digest 与 attestation。

### 阶段 2：增加 Traefik 镜像部署覆盖配置

目标：VPS 使用公开预构建镜像，并保持现有内部网络和 Caddy 路由边界。

范围：

- 新增 `compose.traefik.yaml`，与 `compose.yaml` 通过多个 `-f` 参数合并。
- 对 API、MCP、Web 使用 `build: !reset null`，改为对应 GHCR `image`。
- 使用同一个必填 `TICKLY_IMAGE_TAG` 选择三套镜像版本。
- 对 Web 使用 `ports: !reset []`，不再发布宿主机 `8080`。
- Web 同时加入 Compose 默认网络与 Traefik 外部网络；API/MCP 保持只在默认网络。
- 在 Web 上配置 `traefik.enable=true`、Docker network、Host rule、entrypoint、TLS、certificate resolver、显式 service 和 `server.port=8080`。
- 不给 API/MCP 添加 Traefik labels，不配置 `StripPrefix`。
- 保留 `TICKLY_MCP_ALLOWED_HOSTS` 与 `TICKLY_MCP_ALLOWED_ORIGINS` 对实际 HTTPS 域名的显式要求。

部署变量：

```dotenv
TICKLY_IMAGE_TAG=v1.0.0
TICKLY_DOMAIN=tickly.example.com
TICKLY_TRAEFIK_NETWORK=traefik
TICKLY_TRAEFIK_ENTRYPOINT=websecure
TICKLY_TRAEFIK_CERT_RESOLVER=letsencrypt
```

验收：

- 合并后的三个服务都没有 `build`。
- 合并后的三个服务引用相同 `TICKLY_IMAGE_TAG`。
- Web、API、MCP 都没有宿主机端口映射。
- 只有 Web 加入 Traefik 外部网络。
- Traefik router 明确指向 Web service 的容器端口 `8080`。
- 原有 healthcheck、depends_on、volume、API/MCP 内部地址和安全环境变量全部保留。

### 阶段 3：补齐自动检查与部署文档

目标：让发布和 Traefik 边界可以在后续变更中重复验证。

范围：

- 扩展 `scripts/check-compose.ps1`，增加显式 Traefik 模式，同时保持无参数时的当前检查行为。
- Traefik 模式解析两个 Compose 文件合并后的 JSON，验证镜像、build 清理、端口、网络和 labels。
- Caddy 路由验证继续基于本地构建出的 Web 镜像，不能因为线上改用 GHCR 而删除。
- 更新 `.env.example`，只加入非敏感部署变量和占位值。
- 更新 `README.md`，区分本地构建、GHCR 发布、首次 Public 设置、Traefik 部署、migration、升级与回滚命令。
- 更新 `AGENTS.md` 当前状态与验证要求；不修改历史实施计划中的已完成记录。

验收：

- 基础 Compose 检查仍通过。
- Traefik 合并配置检查通过。
- 文档不要求服务器保存原始 MCP Token。
- 文档明确 `latest` 不是生产部署标签。
- 文档明确 Public 操作不可逆，并要求匿名拉取验证。

### 阶段 4：提交、推送与首次 GHCR 发布

目标：将已验证的配置推送到 GitHub，并获得三套可公开拉取的真实镜像。

范围：

- 只暂存本路线图确认范围内的文件，保留无关工作区改动。
- 提交与推送分别执行；没有明确推送指令时不触发远端 Actions。
- 推送后监控现有 CI 与三个矩阵发布任务，保存 workflow run 和 digest 作为发布证据。
- 首次创建的三个 Package 默认按 Private 处理，不提前声称公开。
- 在每个 Package 的设置页确认源仓库关联后，将 visibility 改为 Public。
- Public 操作逐包确认名称；完成后不再假设可以恢复为 Private。

验收：

- CI 全仓检查成功。
- API、MCP、Web 三个镜像任务全部成功。
- 三个 Package 均显示 Public。
- 未登录 GHCR 的环境可以拉取三个镜像。
- `docker buildx imagetools inspect` 显示 amd64/arm64 manifest。
- 三个 digest 与同一源 commit 对应。

### 阶段 5：真实 Traefik 部署与运行时 smoke

目标：证明公开镜像、Compose 合并、Traefik、Caddy、API 与 MCP 在真实 HTTPS 入口形成完整闭环。

部署顺序：

1. 在 VPS 创建或确认 Traefik 外部网络。
2. 准备服务器 `.env`，包含镜像标签、Traefik 参数、JWT 密钥、MCP Token 摘要和 Host/Origin 白名单。
3. 拉取指定版本镜像。
4. 使用同一组合 Compose 文件运行 Alembic upgrade。
5. 启动 API、MCP、Web。
6. 等待 Compose healthcheck 和 API/MCP readiness。
7. 通过真实 HTTPS 域名执行 Web、认证、API 与 MCP smoke。

验收：

- `https://<domain>/` 返回 Web，SPA 深链接可以回退到 `index.html`。
- `/health` 返回 API 存活状态，`/ready` 同时确认数据库和 migration。
- `/internal/*` 固定返回 `404`。
- `/mcp` 未携带 Bearer Token 时被拒绝。
- 错误 Host 返回 `421`，错误 Origin 返回 `403`。
- 合法 Codex MCP 客户端可以完成至少一个只读工具调用。
- 登录、refresh、logout 在 HTTPS 下维持 Secure、HttpOnly、SameSite=Strict Cookie 契约。
- API、MCP、Web 三个运行容器均为非 root。
- 宿主机不暴露 `8080`、`8321` 或 `8322`。

### 阶段 6：升级与回滚演练

目标：证明发布链路不仅能首次启动，也能安全升级和回滚。

范围：

- 使用新的 `v*` 或 `sha-*` 标签执行一次升级。
- 升级前备份 SQLite volume，并记录当前 migration revision 和三个镜像 digest。
- 先运行目标版本 migration，再更新容器。
- 应用 smoke 失败时按 migration 可逆性决定应用回滚或数据库恢复，不自动执行破坏性 downgrade。
- 将 `TICKLY_IMAGE_TAG` 回退到上一已知健康版本并重新验证。

验收：

- 升级后数据、认证、Todo 和 MCP 仍可用。
- 回滚使用明确标签或 digest，不依赖 `latest`。
- 备份和恢复结果单独报告，不以容器重新启动代替数据恢复验证。

## 文件影响范围

| 文件 | 计划改动 |
| --- | --- |
| `.github/workflows/ci.yml` | 在现有检查后增加三镜像矩阵发布、标签、缓存和 attestation |
| `compose.traefik.yaml` | 新增 GHCR 镜像与 Traefik 部署覆盖，不复制基础服务配置 |
| `.env.example` | 新增镜像标签、域名、Traefik 网络、entrypoint 和 resolver 示例 |
| `scripts/check-compose.ps1` | 保留默认检查并增加 Traefik 合并模型检查 |
| `README.md` | 增加发布、公开、部署、升级和回滚说明 |
| `AGENTS.md` | 同步当前发布能力与验证边界 |

不计划修改：

- API、MCP、Web 业务实现。
- Caddy 的现有路由结构。
- SQLite 数据模型或 Alembic migration。
- 历史 `docs/superpowers/plans` 实施记录。
- `packages/*`，因为本任务没有跨应用代码消费者。

## 验证矩阵

| 层级 | 检查 | 成功边界 |
| --- | --- | --- |
| 应用 | `mise exec -- pnpm check` | lint、typecheck、Web build 与 Web/API/MCP 测试通过 |
| 基础 Compose | `docker compose config --quiet` | 当前本地构建模型可解析 |
| Traefik Compose | `docker compose -f compose.yaml -f compose.traefik.yaml config --quiet` | 镜像、变量、网络和 labels 合并成功 |
| 仓库约束 | `scripts/check-compose.ps1` 两种模式 | 基础与 Traefik 边界均满足 |
| 本地镜像 | `docker compose build api mcp web` | 三个当前 Dockerfile 可以构建 |
| Actions | CI + matrix publish | 检查先于发布，三套多架构镜像全部成功 |
| GHCR | manifest、attestation、visibility | digest 可追溯且三个包均为 Public |
| 匿名消费 | 未登录环境执行 pull | 公开镜像无需 Registry 凭据 |
| 线上入口 | HTTPS curl、认证、Codex MCP smoke | Traefik/Caddy/API/MCP 完整闭环 |
| 运维 | 升级、备份、回滚 | 明确版本可升级且可回到已知健康状态 |

## 风险与处理

### GHCR 包名已存在但未关联仓库

`GITHUB_TOKEN` 可能没有写入既有包的权限。发布前先查询包设置；需要时先关联 `Fly-Potato/tickly` 并授予 Actions access，不能改用长期 PAT 绕过所有权问题。

### Public 可见性不可逆

首次发布后先核对镜像内容、source label、digest 和仓库关联，再逐个改为 Public。不能把“仓库公开”误认为“Container Package 已公开”。

### 多架构构建只证明可构建

QEMU 构建成功不等于两个架构都完成运行时 smoke。至少在真实 VPS 架构上完成完整运行验证；未来出现第二种真实部署架构时，再补对应运行 smoke。

### 三镜像发布不具备跨 Package 原子性

GitHub Container Registry 不会把 API、MCP、Web 三个包作为一个事务提交。矩阵任务部分失败时，成功任务可能已经写入同组标签；部署门禁必须检查三套镜像都存在、source revision 一致且 workflow 整体成功，不能只看到其中一个镜像更新就开始部署。

### Compose 覆盖漂移

基础 Compose 的端口、环境变量、healthcheck 或依赖关系变化时，必须重新检查合并后的 Traefik 模型。`compose.traefik.yaml` 不复制基础定义，以减少但不能消除漂移风险。

### 两层反向代理的 forwarded headers

当前业务不依赖真实 client IP 或外部 scheme 生成绝对 URL。未来引入真实 IP 限流、OAuth 回调或绝对 URL 时，必须重新设计 Traefik → Caddy → ASGI 的可信代理链，不直接开启不受限制的 forwarded header 信任。

### SQLite 与发布节奏

公开镜像不改变 SQLite 单写实例边界。部署仍保持单 API 实例；涉及 migration 的版本必须先备份并执行 upgrade，再替换应用容器。

## 官方依据

- [GitHub：发布 Docker 镜像](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GitHub：使用 Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [GitHub：配置 Package 访问与可见性](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
- [Docker：Compose merge 与 `!reset`](https://docs.docker.com/reference/compose-file/merge/)
- [Docker：生产环境 Compose 覆盖文件](https://docs.docker.com/compose/how-tos/production/)
- [Traefik：Docker provider](https://doc.traefik.io/traefik/master/reference/install-configuration/providers/docker/)
- [Traefik：Docker labels 路由配置](https://doc.traefik.io/traefik/reference/routing-configuration/other-providers/docker/)

## 完成定义

只有同时满足以下条件，才能把本路线图标记为完成：

- 仓库包含经过验证的 GHCR 发布 job 与 Traefik Compose 覆盖文件。
- CI、三镜像多架构构建、推送和 attestation 全部成功。
- 三个 GHCR Package 已逐一核对并设为 Public。
- 匿名环境可以拉取三个指定版本镜像。
- 真实 VPS 完成 migration、Traefik HTTPS、Web/API/MCP smoke。
- 升级与回滚使用明确版本完成演练。
- README 与 AGENTS.md 准确区分本地构建、已发布能力和实际线上验证状态。
