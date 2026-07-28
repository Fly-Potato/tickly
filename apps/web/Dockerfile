FROM node:24.18.0-bookworm-slim AS builder

RUN corepack enable \
    && corepack prepare pnpm@11.16.0 --activate

WORKDIR /workspace

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @tickly/web...

COPY apps/web apps/web
RUN pnpm --filter @tickly/web build


FROM caddy:2.11.4-alpine AS runtime

# 官方镜像不内置运行账号；固定 UID/GID 以保持非 root 边界和卷权限稳定。
RUN addgroup -S -g 10001 caddy \
    && adduser -S -D -H -u 10001 -G caddy caddy

COPY apps/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=builder --chown=caddy:caddy /workspace/apps/web/dist /srv

RUN chown -R caddy:caddy /config /data /srv

USER caddy

EXPOSE 8080
