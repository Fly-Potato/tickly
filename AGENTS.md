# Tickly 仓库协作指南

## 项目定位

Tickly 是一个计划接入 AI 能力的类 Todo List 项目，采用 monorepo 组织。

当前状态：

- `apps/web` 已具备用户名登录、内存 access token、自动 refresh、认证状态恢复，以及支持 CRUD、筛选、排序、cursor 分页和账号时区的响应式 Todo 工作区与 Vitest 测试。
- `apps/api` 已具备 FastAPI 应用工厂、`/health`、数据库与 migration 感知的 `/ready`、请求 ID、统一错误、结构化日志、SQLAlchemy/SQLite 数据层、Alembic migration、单账号 CLI、JWT/refresh 会话认证，以及受当前用户所有权保护的 Todo API。
- AI 功能尚未实现；不要把规划能力描述成现有能力。

实现新功能前先确认当前任务属于 Web、API 还是真正需要跨应用复用的代码，不为可能出现的需求提前扩展架构。

## 仓库结构

- `apps/web`：React、TypeScript 与 Vite Web 应用。
- `apps/api`：FastAPI 服务，由 uv 管理 Python 环境和依赖。
- `packages/*`：跨 workspace 复用代码的预留位置；只有出现真实的跨应用消费者后才新增包。
- `docs/roadmaps`：跨阶段的产品与工程路线图。
- `docs/superpowers/specs`：已经确认的设计文档。
- `docs/superpowers/plans`：对应设计的实施计划。

## 技术栈与依赖管理

- 根 `mise.toml` 管理 Node.js 24、pnpm 11 和 uv。
- JavaScript workspace 使用 pnpm，唯一锁文件是根 `pnpm-lock.yaml`。
- Web 使用 React 19、TypeScript、Vite、Tailwind CSS 4、shadcn 与 Base UI。
- API 使用 Python 3.13、FastAPI 和 pytest；依赖与锁文件分别是 `apps/api/pyproject.toml` 和 `apps/api/uv.lock`。
- Python 依赖统一通过 uv 管理，不额外维护 pip requirements 文件。
- Docker 使用根目录 `compose.yaml`；API 与 Web 镜像必须保持非 root 运行。
- 新增依赖前先确认现有依赖无法满足需求，并把依赖声明到实际使用它的应用或 workspace 包。

## 常用命令

除非正在排查工具链本身，命令均从仓库根目录执行，并优先使用 mise 提供的版本。

安装工具与依赖：

```bash
mise install
mise exec -- pnpm install
mise exec -- uv sync --project apps/api --locked
```

本地开发：

```bash
mise exec -- pnpm dev:web
mise exec -- pnpm dev:api
```

检查与测试：

```bash
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:web
mise exec -- pnpm test:api
mise exec -- pnpm check
```

格式化会改写文件，只在需要格式化相关 TypeScript 或 TSX 文件时运行：

```bash
mise exec -- pnpm format
```

## 通用开发约定

- 修改范围应聚焦当前任务，不顺带重构无关代码。
- 尊重现有应用和包边界；不要为了预期复用提前创建 `packages/*` 包。
- 优先延续仓库已有模式；引入新的目录层级或抽象前，先证明当前复杂度确实需要它。
- 修改命令、目录、环境要求或公开接口时，同步更新相关文档。
- 不提交密钥、令牌、`.env`、虚拟环境、缓存、构建产物或编辑器临时文件。
- 不覆盖或清理与当前任务无关的用户改动。

## 代码注释与说明规范

- 测试代码中的注释、docstring、测试说明文本、fixture 说明和断言失败说明必须使用中文；测试函数名、变量名、协议字段、API 路径、SQL 关键字和第三方 API 的固定名称可以保留英文。
- 测试中的说明应解释被验证的业务行为、边界条件、数据隔离、失败原因或回滚原因，不要只重复代码表面含义；新增测试不得用无意义的 `test1`、`works` 等描述。
- 非测试代码必须为关键边界补充中文注释，至少覆盖事务边界、数据转换、不变量、权限/安全约束、并发控制、异常恢复、外部依赖和兼容性处理等容易被误改的逻辑。
- 复杂业务逻辑必须补充完整的中文说明，优先使用模块、类或函数 docstring，明确目的、输入输出、状态变化、不变量、异常与回滚、时区/并发假设、敏感数据处理和外部副作用；认证、任务、AI、migration 和跨端数据适配代码默认属于复杂逻辑。
- 注释应说明“为什么这样做”和适用边界，而不是翻译代码；简单赋值、明显的条件判断和逐行复述不需要注释。
- 注释必须与实现同步维护。发现旧注释与代码不一致时，以代码实际行为为准立即修正，不保留过时说明。
- 注释和说明文本统一使用中文；只有代码标识符、标准协议、库名、错误码、SQL/API 固定语法和必要的原文引用可以使用英文。

## Web 开发约定

- 使用 TypeScript 与 React 函数组件，延续现有 `@/` 路径别名。
- 优先复用 `apps/web/src/components/ui` 中的组件、现有 CSS 变量和设计令牌。
- 组件保持单一职责；只有复杂度出现后，再拆分业务状态、网络访问和纯展示逻辑。
- 新增交互时处理加载、空数据、错误、禁用状态以及基本键盘操作。
- 不在浏览器代码中保存服务端密钥或模型供应商凭据。
- access token 只能保存在内存，不写入 `localStorage`、`sessionStorage`、IndexedDB 或其他持久存储；refresh token 只由 HttpOnly Cookie 承载。

## API 开发约定

- FastAPI 入口当前位于 `apps/api/app/main.py`；路由和业务增长后再按明确职责拆分模块。
- 对外请求与响应使用明确类型，接口行为变化必须更新或新增 pytest 测试。
- 通过 uv 和 `pyproject.toml` 管理依赖，不混用其他 Python 依赖来源。
- 保留 `GET /health` 作为不依赖数据库和外部服务的基础健康检查。

## AI 功能边界

AI 功能尚未实现。开始接入时至少遵守以下约束：

- 模型供应商密钥只保存在服务端，Web 必须通过 `apps/api` 使用 AI 能力。
- 先定义清晰的 Web/API 契约，再实现具体模型调用。
- 没有多个真实供应商或调用场景时，不提前构建复杂的通用模型抽象。
- 流式接口需要处理正常完成、服务端错误、客户端取消和连接中断，并覆盖相应测试。
- 向模型发送任务内容前，明确数据最小化、日志记录和敏感信息处理策略。

## 验证要求

根据改动范围执行最小但充分的验证：

- Web 改动：运行 `mise exec -- pnpm lint`、`mise exec -- pnpm typecheck` 和 `mise exec -- pnpm build`。
- API 改动：运行 `mise exec -- pnpm test:api`。
- 认证跨端改动：运行 Web 检查、`mise exec -- pnpm test:web` 和 `mise exec -- pnpm test:api`，并验证真实接口契约。
- 仅文档改动：检查路径、命令、事实和 Markdown 结构，无需运行应用测试。

完成说明必须列出实际运行过的检查及结果；未执行的检查不得描述为通过。
