# Tickly 镜像发布

本文说明仓库当前的 GHCR 镜像发布边界。完整的 VPS 启动、migration、备份、回滚和线上验收见 [Tickly VPS 部署说明](mcp-client-deployment.md)。

## 发布触发

普通 `main` push 只执行受影响范围的检查。只有推送 `v*` tag 时，发布 workflow 才会在全量检查通过后构建并推送镜像。

```bash
git tag v1.2.3
git push origin v1.2.3
```

版本 tag 同时生成 `v1.2.3`、`1.2.3` 和 `sha-*` 标签。生产部署应选择已经验证的版本或 `sha-*`，不要使用会随主线漂移的 `latest`。

## 镜像

发布目标为：

- `ghcr.io/fly-potato/tickly-api`
- `ghcr.io/fly-potato/tickly-mcp`
- `ghcr.io/fly-potato/tickly-web`

三个镜像均构建 `linux/amd64` 与 `linux/arm64` 多架构 manifest。三套镜像由矩阵任务分别发布，不具备跨 Package 原子提交；部署前必须确认三套镜像的目标标签都存在，并且来源 revision 一致。

## 发布前检查

发布 workflow 会先执行仓库全量检查：

```bash
mise exec -- pnpm check
```

本地还可以检查两种 Compose 配置：

```bash
docker compose config --quiet
docker compose -f compose.yaml -f compose.traefik.yaml config --quiet
```

## GHCR 验收边界

首次由 workflow 创建的 GHCR Package 默认按 Private 处理。需要在 GitHub Package 设置中分别确认三个包关联 `Fly-Potato/tickly`，再单独验证：

1. Package 可见性是否符合预期。
2. 未登录 GHCR 的环境能否匿名 pull。
3. digest 与来源提交是否正确。
4. 目标 VPS 上的 HTTPS、Web、API、MCP 和工具调用 smoke 是否通过。

公开仓库本身不等于 GHCR Package 已公开；本地 Compose HTTP 检查也不能替代真实线上验收。

## 生产部署入口

生产使用 `compose.yaml` 与 `compose.traefik.yaml` 的组合配置。Traefik 负责公网 TLS 和域名路由，Web/Caddy 继续处理 `/api/*`、`/mcp`、`/internal/*` 与 SPA fallback。执行部署前请阅读 [VPS 部署说明](mcp-client-deployment.md)。
