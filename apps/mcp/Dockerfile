FROM ghcr.io/astral-sh/uv:python3.13-trixie AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY apps/mcp/pyproject.toml apps/mcp/uv.lock ./
RUN uv sync --frozen --no-dev --no-cache


FROM ghcr.io/astral-sh/uv:python3.13-trixie AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# MCP 不需要写数据库或持久卷，固定独立身份并保持运行时文件只读。
RUN groupadd --system --gid 10002 tickly-mcp \
    && useradd --system --uid 10002 --gid tickly-mcp \
        --home-dir /app --shell /usr/sbin/nologin tickly-mcp

WORKDIR /app

# 运行账号只需读取代码与依赖；保留 root:root 所有权可阻止进程原地改写。
COPY --from=builder /app/.venv /app/.venv
COPY apps/mcp/app /app/app

USER tickly-mcp

CMD ["python", "-m", "app.server"]
