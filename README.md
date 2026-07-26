# Tickly

Tickly is a React application managed as a pnpm monorepo. The web application lives in `apps/web`; reusable workspace packages belong in `packages/*`.

## Setup

Node.js and pnpm are pinned by the root `mise.toml`.

```bash
mise install
mise exec -- pnpm install
```

## Development

Run these commands from the repository root:

```bash
mise exec -- pnpm dev
mise exec -- pnpm build
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm format
mise exec -- pnpm preview
```

The root commands target the `@tickly/web` workspace package through pnpm filters. The single `pnpm-lock.yaml` at the repository root is the source of truth for dependencies.
