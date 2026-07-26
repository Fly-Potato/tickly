# Tickly 中文 AGENTS.md 初始化设计

## 目标

在仓库根目录新增一份中文 `AGENTS.md`，为后续参与 Tickly 开发的智能体提供准确、可执行且与当前仓库一致的协作说明。

文档必须区分当前事实与未来规划：

- Tickly 的产品方向是类 Todo List 应用。
- 当前仓库以 Web 应用为主。
- `apps/api` 已存在，但仅是提供 `GET /health` 的最小 FastAPI 骨架。
- Todo 业务功能和 AI 能力仍处于规划阶段，不能描述为已经实现。

## 文件组织

本次只在仓库根目录创建一个 `AGENTS.md`，统一覆盖整个 monorepo。

当前代码规模较小，根级单文件足以表达共同约定，也能避免根目录、Web 和 API 三份规则之间出现重复或漂移。等各应用形成明显不同的开发流程或约束后，再考虑在子目录增加更具体的 `AGENTS.md`。

## 内容结构

### 项目定位与当前状态

开篇使用中文说明产品目标、现有能力和规划能力。规划中的 Todo 与 AI 功能必须使用“计划”“未来”或“尚未实现”等表述。

### 仓库结构

说明当前主要目录的职责：

- `apps/web`：React 与 Vite Web 应用。
- `apps/api`：由 uv 管理的最小 FastAPI 服务。
- `packages/*`：预留给真正需要跨应用复用的 workspace 包。
- `docs/superpowers`：已确认的设计与实施计划。

禁止仅为了预期复用而提前创建共享包。

### 技术栈与工具链

记录仓库中已经存在的技术选择：

- Node.js 24、pnpm 11、uv，由根 `mise.toml` 管理。
- React 19、TypeScript、Vite、Tailwind CSS 4、shadcn 与 Base UI。
- Python 3.13、FastAPI 与 pytest。
- JavaScript 依赖由根 `pnpm-lock.yaml` 锁定，Python API 依赖由 `apps/api/uv.lock` 锁定。

版本说明以仓库配置为准，不复制容易漂移的完整补丁版本。

### 标准命令

所有标准命令从仓库根目录执行，并优先经过 mise：

```bash
mise install
mise exec -- pnpm install
mise exec -- uv sync --project apps/api --locked
mise exec -- pnpm dev
mise exec -- pnpm dev:api
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:api
```

格式化命令会直接改写文件，因此只在确实需要格式化相关文件时运行：

```bash
mise exec -- pnpm format
```

### 开发约定

通用约定：

- 修改应聚焦当前任务，不顺带重构无关代码。
- 尊重现有目录和包边界；共享代码只有在出现真实的跨应用消费者后才进入 `packages/*`。
- 新增依赖前先确认现有依赖无法满足需求，并将依赖声明到实际使用它的 workspace 或 Python 项目。
- 修改命令、目录、环境要求或公开接口时同步更新相关文档。
- 不提交密钥、令牌、`.env`、虚拟环境、缓存或构建产物。

Web 约定：

- 使用 TypeScript 和函数组件，延续现有 `@/` 路径别名。
- 优先复用 `apps/web/src/components/ui` 中的组件和现有设计令牌。
- 组件保持职责单一；业务状态、网络访问与纯展示逻辑在复杂度出现时再拆分。
- 新增交互需要覆盖加载、空数据、错误和禁用状态，并保持键盘可用性。

API 约定：

- FastAPI 入口继续位于 `apps/api/app/main.py`，规模增长后再按路由、模型和服务职责拆分。
- 对外请求与响应使用明确类型；接口变化需要对应测试。
- Python 依赖只通过 uv 和 `pyproject.toml` 管理，不混用 pip 生成另一套依赖来源。

### AI 接入边界

AI 功能尚未实现。未来开始接入时遵守以下最低边界：

- 模型供应商密钥仅保存在服务端，Web 端不得直连模型供应商或持有密钥。
- Web 与模型之间通过 `apps/api` 定义清晰、可演进的接口契约。
- 在确定存在多个供应商或多个模型调用场景前，不提前设计复杂的通用抽象。
- 流式接口必须考虑正常完成、服务端错误、客户端取消和连接中断，并提供相应测试。
- 向模型发送任务内容前，需要明确数据最小化、日志记录和敏感信息处理策略。

### 验证要求

根据改动范围执行最小但充分的验证：

- Web 改动：`lint`、`typecheck`、`build`。
- API 改动：`test:api`。
- 跨端改动：执行 Web 与 API 两侧检查，并验证实际接口契约。
- 仅文档改动：检查命令、路径、事实和 Markdown 结构，无需运行应用测试。

完成说明必须列出实际运行过的验证及结果，不能把未执行的检查描述为通过。

## 语言与语气

`AGENTS.md` 的说明文字使用简洁中文。代码、文件路径、命令、包名和技术名称保留原文，避免翻译后失去可执行性。

规则使用明确的“应”“不得”“仅在”等措辞，但不重复仓库已有文档中的大段背景信息。文档应能让智能体快速回答三个问题：仓库现在有什么、改动应放在哪里、完成前需要验证什么。

## 验收标准

1. 仓库根目录存在中文 `AGENTS.md`。
2. 文档准确反映 Web、最小 API 骨架及 Todo/AI 尚未实现的状态。
3. 目录、技术栈、依赖管理方式和命令均与当前仓库配置一致。
4. 文档包含通用、Web、API、AI 接入和验证约定。
5. 不引入新的应用代码、依赖、共享包或子目录级 `AGENTS.md`。
6. Markdown 结构清晰，全部内容确定且无未决事项。

## 非目标

本次不实现 Todo 功能、AI 功能、数据库、认证、API 业务路由或 Web 页面，也不调整现有工具链、目录结构和依赖。
