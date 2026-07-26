# Tickly pnpm Monorepo 迁移设计

## 目标

将当前单包 React、Vite 项目迁移为轻量 pnpm monorepo，同时保留现有 Git 历史和应用行为。项目继续使用根目录现有的 `mise.toml` 管理 Node.js 24 与 pnpm 11。

## 仓库结构

```text
tickly/
├── apps/
│   └── web/
│       ├── public/
│       ├── src/
│       ├── components.json
│       ├── eslint.config.js
│       ├── index.html
│       ├── package.json
│       ├── tsconfig.app.json
│       ├── tsconfig.json
│       ├── tsconfig.node.json
│       └── vite.config.ts
├── packages/
│   └── README.md
├── docs/
├── package.json
├── pnpm-workspace.yaml
├── pnpm-lock.yaml
├── mise.toml
└── README.md
```

现有应用文件整体迁入 `apps/web`。应用包命名为 `@tickly/web`，保持私有包和当前 `0.0.1` 版本。`packages/*` 预留给未来的共享 UI、类型或工具包；本次迁移不创建共享配置包。

## Workspace 与依赖

`pnpm-workspace.yaml` 声明以下成员：

```yaml
packages:
  - apps/*
  - packages/*
```

根 `package.json` 保持 `private: true`，声明 `"packageManager": "pnpm@11.16.0"`，并只承担 workspace 命令编排。该版本与当前 `mise.toml` 解析出的 pnpm 11 工具版本一致。现有运行时依赖、Vite、TypeScript、ESLint 和 Prettier 依赖随应用移动到 `apps/web/package.json`，使应用包能够独立声明其直接依赖。

删除 npm 的 `package-lock.json`，由 pnpm 在仓库根目录生成唯一的 `pnpm-lock.yaml`。安装完成后只提交 pnpm lockfile，不同时维护两种包管理器的锁文件。

## 命令接口

根目录保留开发者当前熟悉的命令，并通过 pnpm filter 转发到 `@tickly/web`：

| 根命令 | 行为 |
| --- | --- |
| `pnpm dev` | 启动 Web 应用的 Vite 开发服务器 |
| `pnpm build` | 构建 Web 应用 |
| `pnpm lint` | 运行 Web 应用 ESLint |
| `pnpm typecheck` | 运行 Web 应用 TypeScript 检查 |
| `pnpm format` | 格式化 Web 应用的 TypeScript 与 TSX 文件 |
| `pnpm preview` | 预览 Web 应用生产构建 |

应用包继续提供同名脚本。根命令使用包名过滤，而不是依赖目录路径，避免后续目录调整影响命令接口。

## mise 管理

保留现有 `mise.toml`：

```toml
[tools]
node = "24"
pnpm = "11"
```

迁移不引入 Corepack，也不修改用户已选定的主版本。验证通过 `mise exec -- node --version` 和 `mise exec -- pnpm --version` 完成；依赖安装和项目命令同样在 mise 环境中执行。

## 文档与 Git

根 `README.md` 更新为 monorepo 入口，说明目录结构、mise 初始化、依赖安装和常用根命令。`packages/README.md` 解释该目录的用途，确保预留目录进入 Git。

保留当前 `main` 分支及初始提交。设计文档单独提交，实施迁移再形成后续提交，不重新创建 `.git`，也不重写历史。

## 失败处理

- 如果 mise 中指定的工具尚未安装，先运行 `mise install`，再重试验证。
- 如果 pnpm 安装失败，保留生成的错误信息并检查 manifest 与 workspace 配置，不回退到 npm。
- 如果迁移后的命令解析不到包，检查 `pnpm-workspace.yaml` glob 与 `@tickly/web` 包名是否一致。
- 构建、类型检查或 lint 失败时，在提交迁移前修复由路径移动或配置解析造成的问题；不顺带修改无关业务行为。

## 验证标准

迁移完成必须满足：

1. `git status` 不包含意外文件，且不再存在 `package-lock.json`。
2. mise 激活的 Node.js 主版本为 24，pnpm 主版本为 11。
3. `pnpm install --frozen-lockfile` 在根目录成功。
4. 根目录的 `pnpm build`、`pnpm lint` 和 `pnpm typecheck` 全部成功。
5. pnpm 能识别 `@tickly/web` workspace 包。
6. 应用源码行为和 Vite 构建入口保持不变。

## 非目标

本次不新增后端应用、共享组件包、任务编排器、发布流程、CI 配置或远程仓库，也不调整现有 React 页面功能。
