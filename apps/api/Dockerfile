FROM ghcr.io/astral-sh/uv:python3.13-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-cache


FROM ghcr.io/astral-sh/uv:python3.13-trixie AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 固定运行身份并预置数据库目录，确保镜像和新 volume 都能由非 root 进程写入。
RUN groupadd --system --gid 10001 tickly \
    && useradd --system --uid 10001 --gid tickly \
        --home-dir /app --shell /usr/sbin/nologin tickly \
    && mkdir --parents /data \
    && chown tickly:tickly /data

WORKDIR /app

COPY --from=builder --chown=tickly:tickly /app/.venv /app/.venv
COPY --chown=tickly:tickly apps/api/app /app/app
COPY --chown=tickly:tickly apps/api/alembic.ini /app/alembic.ini
COPY --chown=tickly:tickly apps/api/alembic /app/alembic
COPY --chmod=0555 --chown=root:root apps/api/docker-entrypoint.sh /app/docker-entrypoint.sh

USER tickly

CMD ["/bin/sh", "/app/docker-entrypoint.sh"]
