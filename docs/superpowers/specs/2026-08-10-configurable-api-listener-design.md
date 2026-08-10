# 后端监听地址与端口配置设计

## 背景

Tickly API 当前有两条启动链路：本地开发通过根目录的 `pnpm dev:api` 调用 `fastapi dev`，Docker 镜像则在 `CMD` 中固定使用 `0.0.0.0:8000`。两者没有复用 API 已有的 `TICKLY_` 配置体系，监听地址和端口也不能通过统一配置覆盖。

本次改动让本地开发和容器运行都通过同一套后端配置决定监听 IP 与端口，同时保持现有默认行为。

## 目标

- 使用 `TICKLY_HOST` 配置 API 监听 IP。
- 使用 `TICKLY_PORT` 配置 API 监听端口。
- 本地开发默认继续监听 `127.0.0.1:8000`。
- Docker Compose 默认继续监听容器内的 `0.0.0.0:8000`。
- Compose 内依赖 API 端口的健康检查、Caddy 反向代理和 smoke test 跟随配置变化。
- 对非法 IP 和超出范围的端口在进程启动前给出明确的配置校验错误。

## 非目标

- 不改变 Web 对外发布的 `8080` 端口。
- 不新增通用服务器配置框架，也不提前支持 workers、TLS 或 Unix socket。
- 不改变 API 路由、认证、数据库或业务逻辑。
- 不允许通过配置关闭健康检查或绕过现有生产安全校验。

## 配置契约

在 `app.core.config.Settings` 中新增以下字段：

| 环境变量 | Settings 字段 | 本地默认值 | 校验 |
| --- | --- | --- | --- |
| `TICKLY_HOST` | `host` | `127.0.0.1` | 必须是合法 IPv4 或 IPv6 地址 |
| `TICKLY_PORT` | `port` | `8000` | 必须是 `1` 到 `65535` 的整数 |

`apps/api/.env` 继续由 Pydantic Settings 自动读取，因此复制 `apps/api/.env.example` 后即可覆盖本地监听参数。环境变量仍按现有 Pydantic Settings 优先级覆盖 `.env`。

Docker Compose 显式把默认值设为 `0.0.0.0:8000`，以保持容器可由同一 Compose 网络中的 Web/Caddy 访问。容器场景若把 `TICKLY_HOST` 改为仅回环地址，其他容器将无法访问 API；文档会明确建议 Compose 保持 `0.0.0.0`，通常只调整端口。

## 启动架构

新增 `apps/api/app/server.py` 作为统一启动入口：

1. 创建 `Settings`，在启动网络服务前完成监听参数和现有生产配置校验。
2. 通过 `uvicorn.run()` 使用导入字符串 `app.main:app` 启动服务。
3. 把经过校验的 `host`、`port` 传给 Uvicorn。
4. 只为本地开发入口提供 `--reload` 标志；Docker 生产入口不启用 reload。

根 `dev:api` 脚本改为从 `apps/api` 调用 `python -m app.server --reload`。Docker `CMD` 改为调用同一模块但不传 `--reload`。使用导入字符串可满足 Uvicorn reload 模式对子进程重新导入应用的要求。

应用工厂仍由 `app.main` 管理。服务器启动配置只负责进程监听，不进入 FastAPI 应用状态，也不与请求级配置耦合。

## Docker Compose 联动

Compose 将完成以下联动：

- API 服务接收 `${TICKLY_HOST:-0.0.0.0}` 和 `${TICKLY_PORT:-8000}`。
- API `expose` 使用同一个端口值。
- API 健康检查从容器环境读取实际端口，并始终通过容器内 `127.0.0.1` 请求 `/health`。
- Web 容器接收仅用于 Caddy 上游地址的 API 端口变量。
- Caddy 将请求反向代理到 `api:<配置端口>`。
- smoke test 显式使用一个非默认 API 端口，证明 API 启动、健康检查和 Caddy 转发形成真实闭环。

Dockerfile 中固定端口的启动参数将被移除。`EXPOSE 8000` 只是镜像默认端口元数据，无法表达运行时动态端口，因此删除该固定声明，由 Compose 的 `expose` 描述实际端口。

## 错误处理

- 非法 `TICKLY_HOST`、非整数端口、零端口、负数端口和大于 `65535` 的端口由 Pydantic 在启动前拒绝。
- 配置错误沿用现有 Settings 校验错误形式，不额外吞掉异常或降级到默认值。
- Compose 中端口配置不一致不做静默兼容；所有消费者从同一个 Compose 插值值派生，避免多份配置漂移。

## 测试与验证

实现遵循测试先行：

1. 扩展 `apps/api/tests/test_config.py`，先验证默认值、环境变量覆盖及非法 IP/端口被拒绝。
2. 新增服务器入口测试，验证经过校验的监听参数和 reload 模式被传给 Uvicorn，不启动真实网络服务。
3. 修改生产代码使上述测试通过。
4. 让 Docker smoke 使用非默认 API 端口，验证 Compose 健康检查和 Web/Caddy 到 API 的真实请求链路。

至少执行：

```bash
mise exec -- pnpm test:api
TICKLY_JWT_SECRET=<临时安全值> mise exec -- docker compose config --quiet
mise exec -- pnpm docker:smoke
```

完成说明将分别报告 API 测试、Compose 配置解析和 Docker smoke 的实际结果；如果本机 Docker daemon 不可用，不会把未运行成功的容器验证描述为通过。

## 文档更新

- `apps/api/.env.example` 增加本地默认的 `TICKLY_HOST`、`TICKLY_PORT`。
- 根 `.env.example` 增加 Compose 默认监听配置及容器监听地址说明。
- README 补充本地与 Compose 的配置示例，并说明修改 API 端口不会改变 Web 对外的 `8080` 端口。
