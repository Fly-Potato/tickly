# pnpm Monorepo Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing Vite React application into `apps/web`, establish a pnpm workspace with a root lockfile, and keep Node.js 24 and pnpm 11 managed by mise.

**Architecture:** The root package is a private workspace coordinator with filter-based scripts. The current application becomes the private `@tickly/web` workspace package and retains its Vite, TypeScript, React, Tailwind, shadcn, ESLint, and Prettier configuration locally. `packages/*` is reserved for future shared packages without adding shared-config abstractions now.

**Tech Stack:** mise, Node.js 24, pnpm 11.16.0, pnpm workspaces, React 19, Vite 8, TypeScript 6, Tailwind CSS 4, ESLint 10, Prettier 3.

## Global Constraints

- Preserve the existing `main` branch and Git history; do not reinitialize `.git` or rewrite commits.
- Keep `mise.toml` as the source of truth for `node = "24"` and `pnpm = "11"`.
- Use one root `pnpm-lock.yaml`; remove `package-lock.json` and do not introduce another npm or Yarn lockfile.
- Preserve current application behavior and source imports, including the `@/*` alias.
- Do not add backend code, CI, release automation, or shared configuration packages.
- Preserve the user's uncommitted `mise.toml` change and keep it out of unrelated commits.

### Task 1: Capture the working baseline

**Files:**
- Read only: `package.json`, `mise.toml`, `README.md`, `src/`, `vite.config.ts`, `tsconfig*.json`, `eslint.config.js`, `.prettierrc`, `.prettierignore`

**Interfaces:**
- Consumes: the current single-package npm project and the existing mise configuration.
- Produces: recorded command results that can be compared after the move.

- [ ] **Step 1: Verify the selected mise tool versions**

Run:

```bash
mise exec -- node --version
mise exec -- pnpm --version
```

Expected: Node reports `v24.15.0` and pnpm reports `11.16.0` (or the exact versions resolved by the unchanged `mise.toml`).

- [ ] **Step 2: Run the current application checks**

Run:

```bash
npm run build
npm run lint
npm run typecheck
```

Expected: all three commands pass before any path changes. If a baseline command fails, record that failure and distinguish it from migration regressions.

### Task 2: Move the application into `apps/web`

**Files:**
- Create: `apps/`
- Create: `apps/web/`
- Create: `packages/`
- Move: `src/` → `apps/web/src/`
- Move: `public/` → `apps/web/public/`
- Move: `index.html` → `apps/web/index.html`
- Move: `vite.config.ts` → `apps/web/vite.config.ts`
- Move: `eslint.config.js` → `apps/web/eslint.config.js`
- Move: `components.json` → `apps/web/components.json`
- Move: `tsconfig.json` → `apps/web/tsconfig.json`
- Move: `tsconfig.app.json` → `apps/web/tsconfig.app.json`
- Move: `tsconfig.node.json` → `apps/web/tsconfig.node.json`
- Move: `.prettierrc` → `apps/web/.prettierrc`
- Move: `.prettierignore` → `apps/web/.prettierignore`

**Interfaces:**
- Consumes: the current Vite application files and relative config paths.
- Produces: a self-contained web workspace package whose `src`, Tailwind stylesheet, shadcn aliases, and Vite alias remain relative to `apps/web`.

- [ ] **Step 1: Move tracked application and local tooling files**

Run the equivalent of:

```bash
mkdir -p apps/web packages
git mv src public index.html vite.config.ts eslint.config.js components.json tsconfig.json tsconfig.app.json tsconfig.node.json .prettierrc .prettierignore apps/web/
```

Expected: all listed application files appear under `apps/web`; root-level `mise.toml`, `README.md`, `.gitignore`, and `docs/` remain in place.

- [ ] **Step 2: Verify path-sensitive configuration remains local**

Run:

```bash
rg -n 'src/|\./src|@/|dist|tailwindStylesheet|css' apps/web/{vite.config.ts,tsconfig.json,tsconfig.app.json,components.json,.prettierrc,eslint.config.js}
```

Expected: aliases and stylesheet references still point to `src` relative to `apps/web`; no root-relative path is introduced.

### Task 3: Create workspace manifests and generate the pnpm lockfile

**Files:**
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `apps/web/package.json`
- Create: `pnpm-lock.yaml`
- Delete: `package-lock.json`

**Interfaces:**
- Consumes: the moved application files from Task 2 and the existing dependency declarations.
- Produces: root commands that target the exact workspace package `@tickly/web`, plus a single root pnpm lockfile.

- [ ] **Step 1: Create the application manifest**

Create `apps/web/package.json` by preserving the current manifest's dependencies and scripts, changing only the package identity and keeping the package private:

```json
{
  "name": "@tickly/web",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "format": "prettier --write \"**/*.{ts,tsx}\"",
    "typecheck": "tsc --noEmit",
    "preview": "vite preview"
  }
}
```

Copy the current `dependencies` and `devDependencies` objects into this manifest unchanged.

Expected: the web package declares every dependency imported by its source or used by its local scripts.

- [ ] **Step 2: Create the root workspace manifest**

Create the root `package.json`:

```json
{
  "name": "tickly",
  "private": true,
  "packageManager": "pnpm@11.16.0",
  "scripts": {
    "dev": "pnpm --filter @tickly/web dev",
    "build": "pnpm --filter @tickly/web build",
    "lint": "pnpm --filter @tickly/web lint",
    "format": "pnpm --filter @tickly/web format",
    "typecheck": "pnpm --filter @tickly/web typecheck",
    "preview": "pnpm --filter @tickly/web preview"
  }
}
```

Expected: the root has no application dependencies and every developer-facing command targets `@tickly/web` by package name.

- [ ] **Step 3: Declare workspace globs**

Create `pnpm-workspace.yaml`:

```yaml
packages:
  - apps/*
  - packages/*
```

Expected: pnpm recognizes `apps/web` and future first-level packages under `packages/`.

- [ ] **Step 4: Remove the npm lockfile and generate pnpm metadata**

After confirming the old lockfile is exactly `package-lock.json` at the repository root, delete it and run:

```bash
rm package-lock.json
mise exec -- pnpm install
```

Expected: pnpm creates `pnpm-lock.yaml` at the repository root and installs the `@tickly/web` workspace dependencies. Do not restore or regenerate `package-lock.json`.

- [ ] **Step 5: Check workspace discovery**

Run:

```bash
mise exec -- pnpm list --depth -1 --recursive
mise exec -- pnpm --filter @tickly/web exec pwd
```

Expected: the recursive list includes `@tickly/web`, and the filtered command resolves to `apps/web`.

### Task 4: Document the new workspace entry points

**Files:**
- Modify: `README.md`
- Create: `packages/README.md`

**Interfaces:**
- Consumes: the root command names and directory structure established in Tasks 2–3.
- Produces: onboarding instructions that use mise and pnpm from the repository root.

- [ ] **Step 1: Replace the template README with project setup instructions**

Update `README.md` to include:

```markdown
# Tickly

## Setup

```bash
mise install
mise exec -- pnpm install
```

## Development

```bash
mise exec -- pnpm dev
```

The web application lives in `apps/web`. Future shared packages belong in `packages/*`.
```

Also document the root `build`, `lint`, `typecheck`, `format`, and `preview` commands and state that Node.js and pnpm are managed by `mise.toml`.

Expected: a new contributor can install the pinned tools and dependencies without using npm.

- [ ] **Step 2: Reserve the shared-package directory**

Create `packages/README.md`:

```markdown
# Shared packages

Place reusable workspace packages here, such as shared UI components, types, or utilities. Each package should have its own `package.json` and is included by the root `pnpm-workspace.yaml`.
```

Expected: `packages/` is tracked by Git while remaining free of runtime code in this migration.

### Task 5: Verify the migrated workspace and commit the implementation

**Files:**
- Verify: `mise.toml`, `package.json`, `apps/web/package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `README.md`, `packages/README.md`

**Interfaces:**
- Consumes: the complete workspace from Tasks 2–4.
- Produces: a validated monorepo migration commit with no unrelated changes; the pre-existing uncommitted `mise.toml` remains uncommitted.

- [ ] **Step 1: Reinstall from the lockfile**

Run:

```bash
mise exec -- pnpm install --frozen-lockfile
```

Expected: installation exits successfully without changing `pnpm-lock.yaml`.

- [ ] **Step 2: Run all root verification commands**

Run:

```bash
mise exec -- pnpm build
mise exec -- pnpm lint
mise exec -- pnpm typecheck
```

Expected: all commands pass and the build output is generated under `apps/web/dist`.

- [ ] **Step 3: Confirm tool versions and repository state**

Run:

```bash
mise exec -- node --version
mise exec -- pnpm --version
git status --short
git diff --check
test ! -e package-lock.json
test -f pnpm-lock.yaml
```

Expected: Node is on major 24, pnpm is on major 11, the only pre-existing uncommitted file is `mise.toml`, and the npm lockfile is absent.

- [ ] **Step 4: Commit the migration files**

Run:

```bash
git add .gitignore README.md apps packages package.json pnpm-workspace.yaml pnpm-lock.yaml
git diff --cached --check
git commit -m "chore: migrate to pnpm monorepo"
```

Expected: the commit contains only the monorepo migration and documentation; `mise.toml` is left out because it was already uncommitted before this work.

## Self-review checklist

- [ ] Every file in the design specification has a corresponding plan task.
- [ ] No step depends on a package name other than `@tickly/web` or a path not defined above.
- [ ] The old npm lockfile is removed only after its exact path is confirmed.
- [ ] Verification runs through the root pnpm filter scripts and checks the app-local configs.
