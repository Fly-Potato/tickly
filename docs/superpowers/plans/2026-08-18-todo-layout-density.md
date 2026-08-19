# Todo Layout Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Todo 工作区桌面最大宽度扩大到 1440px，收紧根任务与子任务条目高度，并将桌面编辑抽屉扩大到 640px / 50vw。

**Architecture:** 保持现有 React 组件与业务状态不变，只修改 `index.css` 中的布局令牌和 `TaskEditorPanel` 的响应式宽度类。新增一个源代码级样式契约测试，直接约束 Tailwind `@apply` 与抽屉类名，确保纯视觉尺寸也能回归。

**Tech Stack:** React 19、TypeScript、Tailwind CSS 4、Base UI Dialog、Vitest 4

---

## 文件结构

- 新建 `apps/web/src/features/tasks/layout-density.test.ts`：约束页面、两栏工作区、任务行和编辑抽屉的目标尺寸。
- 修改 `apps/web/src/index.css`：调整桌面页面最大宽度、筛选栏宽度、根任务与子任务的垂直内边距。
- 修改 `apps/web/src/features/tasks/task-editor-panel.tsx`：调整桌面编辑抽屉宽度，保留移动端全宽底部形态。

### Task 1: 建立排版尺寸契约

**Files:**
- Create: `apps/web/src/features/tasks/layout-density.test.ts`
- Test: `apps/web/src/features/tasks/layout-density.test.ts`

- [ ] **Step 1: 写入失败的样式契约测试**

```ts
/// <reference types="node" />

import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import editorSource from "./task-editor-panel.tsx?raw"
import { describe, expect, it } from "vitest"

const todoStyles = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8")

describe("Todo 列表排版密度", () => {
  it("桌面工作区使用 1440px 主体和 272px 筛选栏", () => {
    expect(todoStyles).toMatch(
      /\.todo-shell\s*\{[^}]*max-w-\[90rem\]/s
    )
    expect(todoStyles).toMatch(
      /\.todo-workspace-layout\s*\{[^}]*lg:grid-cols-\[17rem_minmax\(0,1fr\)\]/s
    )
  })

  it("根任务和子任务使用更紧凑的垂直间距", () => {
    expect(todoStyles).toMatch(
      /\.task-row\s*\{[^}]*px-4 py-3[^}]*sm:px-6/s
    )
    expect(todoStyles).toMatch(
      /\.child-task-section \.task-row\s*\{[^}]*py-2\.5/s
    )
  })

  it("桌面编辑抽屉扩大且移动端仍为全宽", () => {
    expect(editorSource).toContain("w-full")
    expect(editorSource).toContain("md:w-[min(40rem,50vw)]")
  })
})
```

- [ ] **Step 2: 运行测试并确认因旧尺寸失败**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/layout-density.test.ts
```

Expected: FAIL；失败信息分别指出找不到 `max-w-[90rem]`、`17rem`、紧凑 `py` 值或 `md:w-[min(40rem,50vw)]`。

### Task 2: 应用 B 方案尺寸

**Files:**
- Modify: `apps/web/src/index.css:228`
- Modify: `apps/web/src/index.css:259`
- Modify: `apps/web/src/index.css:378`
- Modify: `apps/web/src/index.css:382`
- Modify: `apps/web/src/features/tasks/task-editor-panel.tsx:222`
- Test: `apps/web/src/features/tasks/layout-density.test.ts`

- [ ] **Step 1: 扩大桌面主体与筛选栏**

在 `apps/web/src/index.css` 中将对应规则改为：

```css
.todo-shell {
  @apply mx-auto min-h-[calc(100svh-2rem)] max-w-[90rem] overflow-clip rounded-3xl border border-border/75 bg-card/88 shadow-[0_30px_90px_-45px_oklch(0.27_0.08_250/0.35)] backdrop-blur sm:min-h-[calc(100svh-3.5rem)];
}

.todo-workspace-layout {
  @apply grid min-h-[calc(100svh-8rem)] lg:grid-cols-[17rem_minmax(0,1fr)];
}
```

- [ ] **Step 2: 收紧任务条目垂直间距**

在 `apps/web/src/index.css` 中将对应规则改为：

```css
.child-task-section .task-row {
  @apply py-2.5;
}

.task-row {
  @apply grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-3 px-4 py-3 transition-colors hover:bg-blue-50/55 sm:px-6;
}
```

- [ ] **Step 3: 扩大桌面编辑抽屉**

在 `TaskEditorPanel` 的 `Dialog.Popup` 上保留移动端 `w-full`，并把桌面宽度类替换为：

```tsx
md:w-[min(40rem,50vw)]
```

- [ ] **Step 4: 运行样式契约测试并确认通过**

Run:

```bash
mise exec -- pnpm --filter @tickly/web exec vitest run src/features/tasks/layout-density.test.ts
```

Expected: PASS，3 个排版尺寸用例全部通过。

### Task 3: 完整验证与差异复核

**Files:**
- Verify: `apps/web/src/features/tasks/layout-density.test.ts`
- Verify: `apps/web/src/index.css`
- Verify: `apps/web/src/features/tasks/task-editor-panel.tsx`

- [ ] **Step 1: 运行 Web lint**

Run:

```bash
mise exec -- pnpm lint
```

Expected: exit code 0，无 ESLint 错误。

- [ ] **Step 2: 运行 Web typecheck**

Run:

```bash
mise exec -- pnpm typecheck
```

Expected: exit code 0，无 TypeScript 错误。

- [ ] **Step 3: 运行 Web build**

Run:

```bash
mise exec -- pnpm build
```

Expected: exit code 0，Vite 构建完成。

- [ ] **Step 4: 复核范围与格式**

Run:

```bash
git diff --check -- apps/web/src/index.css apps/web/src/features/tasks/task-editor-panel.tsx apps/web/src/features/tasks/layout-density.test.ts
git diff -- apps/web/src/index.css apps/web/src/features/tasks/task-editor-panel.tsx apps/web/src/features/tasks/layout-density.test.ts
```

Expected: 无 whitespace error；差异仅包含已确认的四组尺寸与对应测试，不包含业务逻辑、颜色、字号或移动端断点变化。

> Git 提交不属于本计划的自动执行步骤；仅在用户明确说“提交”后按精确路径暂存并提交。
