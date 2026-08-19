/// <reference types="node" />

import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import createSource from "./task-create-panel.tsx?raw"
import editorSource from "./task-editor-panel.tsx?raw"
import { describe, expect, it } from "vitest"

const todoStyles = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8")

describe("Todo 列表排版密度", () => {
  it("桌面工作区使用 1440px 主体和 272px 筛选栏", () => {
    expect(todoStyles).toMatch(/\.todo-shell\s*\{[^}]*max-w-\[90rem\]/s)
    expect(todoStyles).toMatch(
      /\.todo-workspace-layout\s*\{[^}]*lg:grid-cols-\[17rem_minmax\(0,1fr\)\]/s
    )
  })

  it("列表使用固定六列表格、弱表头和同 DOM 移动折叠", () => {
    expect(todoStyles).toMatch(/\.task-table\s*\{[^}]*table-layout: fixed/s)
    expect(todoStyles).toMatch(
      /\.task-table-head th\s*\{[^}]*text-muted-foreground/s
    )
    expect(todoStyles).toMatch(
      /@media \(max-width: 63\.999rem\)[\s\S]*\.task-row\s*\{[^}]*grid-template-areas/s
    )
    expect(todoStyles).not.toContain(".quick-create {")
  })

  it("桌面新建和编辑抽屉扩大且移动端仍为全宽", () => {
    expect(createSource).toContain("w-full")
    expect(createSource).toContain("md:w-[min(40rem,50vw)]")
    expect(editorSource).toContain("w-full")
    expect(editorSource).toContain("md:w-[min(40rem,50vw)]")
  })
})
