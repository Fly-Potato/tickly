# Tickly

Tickly is a pnpm monorepo containing a React frontend and a minimal FastAPI backend.

- `apps/web`: React and Vite frontend
- `apps/api`: FastAPI backend managed by uv
- `packages/*`: reusable workspace packages

## Setup

Node.js, pnpm, and uv are pinned by the root `mise.toml`. uv manages the API's Python 3.13 environment and dependencies.

Install all pinned tools:

```bash
mise install
```

Install JavaScript and Python dependencies:

```bash
mise exec -- pnpm install
mise exec -- uv sync --project apps/api --locked
```

## Development

Run the Web application from the repository root:

```bash
mise exec -- pnpm dev
```

Run the API from the repository root:

```bash
mise exec -- pnpm dev:api
```

The API exposes `GET /health` and FastAPI's default `/docs`, `/redoc`, and `/openapi.json` routes.

## Checks

```bash
mise exec -- pnpm build
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm format
mise exec -- pnpm test:api
```

The API's Python dependencies are locked in `apps/api/uv.lock`; JavaScript dependencies remain locked in the root `pnpm-lock.yaml`.
