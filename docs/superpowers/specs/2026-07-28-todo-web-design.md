# Tickly 阶段 4：Todo Web 设计

## 1. 目标

在现有 React 认证壳层和阶段 3 Todo API 上，交付可在桌面与手机浏览器日常使用的任务工作区。

本阶段完成后，已登录用户可以快速新增、浏览、筛选、排序、分页、编辑、完成、取消完成和删除任务。任务始终从服务端读取，页面刷新或更换设备后使用同一账号看到一致数据。

## 2. 当前基础

现有 Web 已提供：

- React 19、TypeScript 6、Vite 8、Tailwind CSS 4。
- shadcn、Base UI、Lucide 和项目内 `Button` primitive。
- 用户名登录、内存 access token、refresh 自动恢复和退出。
- 统一的 `apiFetch`，遇到 `401 authentication_required` 时自动 refresh 一次。
- `AuthenticatedShell` 登录后壳层。
- Vitest、jsdom、Testing Library 和中文行为测试。

阶段 3 API 已提供：

```text
POST   /api/v1/tasks
GET    /api/v1/tasks
GET    /api/v1/tasks/{task_id}
PATCH  /api/v1/tasks/{task_id}
DELETE /api/v1/tasks/{task_id}
```

列表 API 支持状态筛选、三种排序、升降序、`limit` 和稳定 cursor，不返回 `total`。

## 3. 范围

### 3.1 包含

- 登录后的任务工作区。
- 只输入标题并按 Enter 提交的快速新增。
- All、Active、Completed 状态筛选。
- 创建时间、截止时间和优先级排序，以及升降序选择。
- cursor 驱动的“加载更多”。
- 标题、备注、优先级和截止时间编辑。
- 完成与取消完成的乐观更新和失败回滚。
- 删除确认。
- 使用账号 IANA 时区展示和编辑截止时间。
- 桌面右侧编辑面板和手机底部编辑抽屉。
- 加载、空数据、错误、重试、禁用和键盘状态。
- Web 行为测试、API 契约测试和文档校准。

### 3.2 不包含

- 数据导出。
- Todo API、认证协议、数据库模型或 migration 修改。
- AI、自然语言任务草稿或模型调用。
- 离线缓存、冲突合并、实时推送或 WebSocket。
- 搜索、拖拽排序、标签、项目、子任务、批量操作或提醒。
- 第二个业务页面或客户端路由体系。
- 全局任务 Store、通用请求框架或通用 repository。
- 任务、access token 或任务查询状态的浏览器持久化。

## 4. 方案比较

### 4.1 功能内聚和 React 本地状态（采用）

任务 API、状态 hook 和展示组件都放入 `features/tasks`。`TodoWorkspace` 是唯一业务容器，使用 feature hook 或 reducer 管理列表和 mutation 状态。

优点：

- 延续现有 React 和 `apiFetch` 模式。
- 不增加全局 Provider 或请求库。
- 状态边界与当前单页面规模匹配。
- 测试可以围绕真实用户行为编写。

代价：

- cursor 合并、请求竞争和乐观回滚需要显式实现。

### 4.2 Tasks Context（不采用）

Context 适合多个相距较远的消费者共享任务状态。当前只有一个工作区，引入 Provider 会扩大状态生命周期并增加不必要的重渲染边界。

### 4.3 TanStack Query 与表单库（不采用）

成熟库能提供缓存和 mutation 管理，但当前请求规模不需要通用缓存层。新增依赖和项目模式的成本高于收益。

## 5. 架构与组件边界

```text
AuthenticatedShell
└── TodoWorkspace
    ├── WorkspaceHeader
    ├── QuickCreateForm
    ├── TaskToolbar
    ├── TaskList
    │   └── TaskRow
    ├── TaskEditorPanel
    └── DeleteTaskDialog

features/tasks/
├── task-api.ts
├── task-time.ts
├── use-task-workspace.ts
├── todo-workspace.tsx
├── quick-create-form.tsx
├── task-toolbar.tsx
├── task-list.tsx
├── task-row.tsx
├── task-editor-panel.tsx
└── delete-task-dialog.tsx

lib/
└── api-error.ts
```

职责：

- `task-api.ts`：任务 DTO、query 类型，以及创建、列表、更新和删除 API 的类型化封装。详情接口当前没有独立消费者，不提前增加 wrapper。
- `task-time.ts`：UTC、账号 IANA 时区和 `datetime-local` 值之间的转换。
- `use-task-workspace.ts`：query、分页、选中任务、请求竞争和 mutation 状态。
- `TodoWorkspace`：组合工作区并把状态与回调分发给展示组件。
- 展示组件：只处理语义结构、字段状态和用户事件，不直接调用 API。
- `lib/api-error.ts`：从现有 `auth-api.ts` 提取中性的错误 envelope、`ApiError` 和响应解析；认证 refresh 行为仍留在 `auth-api.ts`。

不把任务状态加入 Auth Context。`AuthenticatedShell` 只提供当前用户、退出入口和工作区外壳。

## 6. 响应式布局

### 6.1 桌面

```text
┌──────────────────────────────────────────────────────────────┐
│ [T] Tickly                         potato · Asia/Shanghai  退出 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  今天要完成什么？                                             │
│  ┌──────────────────────────────────────────────┐ [添加]     │
│  │ 输入任务标题，按 Enter 创建                   │            │
│  └──────────────────────────────────────────────┘            │
│                                                              │
│  [全部] [进行中] [已完成]     排序：[创建时间 ▼] [降序 ▼]    │
│                                                              │
│  ┌──────────────────────────────┬──────────────────────────┐  │
│  │ □ 完成阶段 4                 │ 编辑任务                 │  │
│  │   高优先级 · 今天 18:00      │ 标题                     │  │
│  ├──────────────────────────────┤ 备注                     │  │
│  │ ✓ 整理 API 测试              │ 优先级                   │  │
│  │   已完成                      │ 截止时间                 │  │
│  ├──────────────────────────────┤                          │  │
│  │          加载更多             │ [取消] [保存]            │  │
│  └──────────────────────────────┴──────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

任务主体点击后打开右侧编辑面板，列表仍可见但编辑层保持焦点边界。完成复选框是独立操作，不打开面板。

### 6.2 手机

```text
┌──────────────────────────┐
│ [T] Tickly        [账号]  │
├──────────────────────────┤
│ 今天要完成什么？          │
│ [任务标题____________][+] │
│                          │
│ [全部][进行中][已完成]    │
│ [创建时间 ▼] [降序 ▼]    │
│                          │
│ □ 完成阶段 4             │
│   高优先级 · 今天 18:00   │
│                          │
│        [加载更多]         │
├──────────────────────────┤
│ 编辑任务抽屉             │
│ 标题 / 备注 / 优先级      │
│ 截止时间                  │
│ [删除]      [取消] [保存] │
└──────────────────────────┘
```

手机使用相同编辑内容，从底部打开抽屉。响应式差异由 CSS 和项目内 UI primitive 处理，不复制两套业务组件。

## 7. 外部数据契约

Web 使用与阶段 3 一致的任务类型：

```text
Task:
  id: string
  title: string
  notes: string | null
  is_completed: boolean
  priority: none | low | medium | high
  due_at: UTC ISO string | null
  completed_at: UTC ISO string | null
  created_at: UTC ISO string
  updated_at: UTC ISO string

TaskPage:
  items: Task[]
  next_cursor: string | null
```

默认列表 query：

```text
status=all
sort=created_at
order=desc
limit=50
```

请求规则：

- 快速新增只发送 `{ title }`。
- 编辑只把一次保存中实际修改的允许字段放入同一个 PATCH。
- 清空备注或截止时间分别发送 `notes: null` 或 `due_at: null`。
- 完成复选框只发送 `{ is_completed }`。
- Web 不发送或读取 `user_id`。
- DELETE 成功按 `204` 处理，不解析响应体。

所有请求复用 `apiFetch`，认证 refresh 最终失败时由现有 Auth Context 返回登录页。

## 8. 状态模型

工作区维护：

```text
query:
  status
  sort
  order

page:
  items
  nextCursor
  initialLoading
  loadingMore
  error

interaction:
  selectedTaskId
  creating
  saving
  deleting
  completingTaskIds
```

不维护 `total`，也不根据当前已加载数量推断总数。

## 9. 读取与分页流程

### 9.1 首次和 query 变化

1. 生成新的 query generation，并取消上一个列表 `AbortController`。
2. 清空旧 items、cursor 和列表错误。
3. 请求第一页。
4. 只允许当前 generation 的响应写入状态。
5. 若选中任务不再位于当前结果，关闭编辑面板。

### 9.2 加载更多

1. 使用当前 `nextCursor` 和相同 status、sort、order 请求下一页。
2. 成功后按任务 ID 去重并追加。
3. 用响应的新 cursor 替换旧 cursor。
4. 失败时保留已加载 items 和原 cursor，在列表底部提供重试。

UI 不使用页码或自动无限滚动。

## 10. Mutation 流程

### 10.1 快速新增

- 标题去除首尾空白后为空时不提交。
- Enter 和添加按钮使用同一提交函数。
- 请求期间禁用输入和按钮，避免重复创建。
- 成功后清空输入并重新加载当前 query，确保新任务按当前排序出现或被当前筛选正确排除。
- 失败时保留输入并在表单附近显示安全错误。

### 10.2 编辑保存

- 打开面板时以当前任务建立独立表单快照。
- 没有修改时禁用保存。
- 一次 PATCH 只提交实际修改字段。
- 成功后关闭面板并重新加载当前 query，处理优先级、截止时间或状态改变造成的重新排序与筛选移除。
- 失败时保留表单和面板，不用旧列表响应覆盖用户输入。
- 表单有未保存修改时，关闭、按 Escape 或切换任务需要确认放弃。

### 10.3 完成与取消完成

- 保存原任务、原索引和当前 query generation。
- 立即更新完成状态；在 Active 或 Completed 筛选中立即移出不再匹配的任务。
- 同一任务请求期间禁用其复选框。
- 成功后使用服务端任务响应替换仍存在的本地任务。
- 失败且 generation 未变化时恢复原任务和原位置。
- generation 已变化时不向新 query 注入旧快照，仅显示错误并重新加载当前 query。

### 10.4 删除

- 删除入口只在编辑面板中出现。
- 用户必须在独立确认对话框中确认，确认内容显示任务标题。
- 成功后关闭确认框和编辑面板，并从当前 items 移除任务。
- 失败时关闭确认框、保留编辑面板并显示安全错误。

## 11. 时区语义

- API 时间始终是 UTC ISO 8601。
- 展示和编辑始终使用 `/api/v1/auth/me` 返回的 `user.timezone`。
- 不使用浏览器当前时区覆盖账号时区，保证不同设备显示一致。
- `datetime-local` 不携带时区，转换集中在 `task-time.ts`。
- 新增 `@internationalized/date`，只用于 IANA 时区和本地墙上时间转换。
- 截止时间输入使用分钟精度，提交时转换为 UTC。
- 夏令时跳过的不存在时间拒绝保存并显示字段错误。
- 夏令时回拨产生的重复时间选择较早的那个时刻，并在测试中固定该语义。
- 空输入转换为 `due_at: null`。

## 12. 错误、加载与可访问性

| 场景 | 界面行为 |
| --- | --- |
| 首次加载 | 显示任务行骨架，筛选暂时禁用 |
| 首次加载失败 | 显示完整错误卡片和“重新加载” |
| 当前筛选无数据 | 显示对应空状态，保留快速新增和筛选 |
| 加载更多失败 | 保留已有任务，在列表底部显示“重试” |
| 新增失败 | 保留标题输入，在输入附近显示错误 |
| 保存失败 | 编辑面板保持打开，保留未保存字段 |
| 删除失败 | 确认框关闭，编辑面板保留并显示错误 |
| 完成切换失败 | 恢复任务原状态和原位置，并提示失败 |
| 认证失效 | 复用 refresh；最终失败后回到登录页 |

规则：

- 每个请求只禁用相关控件，不冻结整个页面。
- 不回显未知异常、请求体、任务正文以外的服务端内部信息。
- 不新增全局 toast；错误靠近触发位置展示。
- 页面级和 mutation 状态通过 `aria-live` 通知辅助技术。
- 任务复选框具有包含任务标题的可访问名称。
- 任务主体、筛选、排序、加载更多和编辑操作支持键盘。
- 编辑面板和删除确认复用 Base UI 对话框能力，处理焦点锁定、Escape 和焦点恢复。
- 完成任务使用弱化样式和删除线，同时保持足够对比度。
- `prefers-reduced-motion` 下关闭非必要过渡。

## 13. 样式方向

- 延续现有 Tickly 蓝黑品牌标记、Inter 字体、圆角卡片和浅色背景光晕。
- 工作区比登录页信息密度更高，但保持单一主列和清晰留白。
- 优先级使用文字和轻量色彩共同表达，不只依赖颜色。
- 截止时间过期、今天和未来使用明确文本，不只使用图标。
- 桌面最大宽度受控；手机触控目标至少 44px。
- 新 UI primitive 放入 `apps/web/src/components/ui`，不创建 workspace 包。

## 14. 测试策略

### 14.1 API 封装

`task-api.test.ts` 覆盖：

- 创建、列表、更新和删除 operation 的方法、路径、请求体和查询参数。
- cursor 参数正确编码。
- DELETE 204 不解析 JSON。
- 非成功响应转换为安全 `ApiError`。
- 认证 refresh 继续由现有 `apiFetch` 测试保护。

### 14.2 时间转换

`task-time.test.ts` 覆盖：

- UTC 到 `Asia/Shanghai` 输入和展示值。
- 本地墙上时间到 UTC。
- UTC、具有夏令时的 IANA 时区和跨日转换。
- 不存在时间拒绝。
- 重复时间选择较早时刻。
- 空截止时间。

### 14.3 工作区行为

`todo-workspace.test.tsx` 覆盖：

- 初次加载、骨架、空状态、失败和重试。
- Enter 快速新增、标题保留和重复提交保护。
- 状态筛选、排序、旧请求取消和旧响应忽略。
- 加载更多追加、ID 去重、末页和失败重试。
- 编辑面板打开、显式保存、取消和放弃未保存修改。
- 清空备注与截止时间。
- 删除确认、成功和失败保留。
- 完成/取消完成乐观更新、筛选移除、成功替换和失败回滚。
- 关键键盘操作和可访问名称。

### 14.4 壳层验收

- 认证用户进入 Todo 工作区。
- 用户名、账号时区和退出入口仍可见。
- 退出继续清除内存 token 并回到登录页。

jsdom 不断言实际像素和 CSS media query 布局；自动化测试验证语义结构和抽屉交互，响应式视觉由构建和人工验收确认。

## 15. 依赖与代码边界

- 继续复用 React、Tailwind、shadcn、Base UI、Lucide 和 `apiFetch`。
- 只新增 `@internationalized/date`，声明在 `apps/web/package.json` 并更新根锁文件。
- 把通用安全错误解析提取到 `apps/web/src/lib/api-error.ts`，认证与任务 API 共同复用，但不改变现有 refresh 语义。
- 不引入路由器、全局 Store、TanStack Query、表单库或 toast 系统。
- 不创建 `packages/*` 包。
- 不修改 `apps/api` 生产代码、migration 或锁文件。

## 16. 文档校准

- README 把 Todo Web 标记为已实现只发生在阶段 4 全部验收完成后。
- AGENTS 当前状态在实施完成后校准为已有 Todo Web。
- 路线图阶段 4 删除数据导出范围和验收；本设计明确不做导出。
- 阶段 4 完成后，路线图下一阶段指向阶段 5 AI 任务草稿。

## 17. 验证

开发环境执行：

```powershell
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:web
mise exec -- pnpm test:api tests/test_tasks_api.py -q
```

范围检查：

```powershell
rg -n "localStorage|sessionStorage|IndexedDB|createObjectURL|数据导出" apps/web/src/features/tasks apps/web/src/features/auth -g "!*.test.ts" -g "!*.test.tsx"
rg -n "TanStack|react-router" apps/web/src
git diff --check
git status --short
```

Docker、API schema 和 migration 不变时不重复扩大本阶段 smoke 范围。

## 18. 验收标准

- 桌面和手机布局可以完成全部任务核心操作。
- 页面刷新和不同设备读取同一服务端数据。
- Enter 可以快速创建标题任务，不产生重复提交。
- 筛选、三种排序和升降序与 API 契约一致。
- 大量任务通过“加载更多”逐页读取，不一次请求全部数据。
- 编辑使用显式保存和取消；关闭脏表单前确认放弃。
- 完成切换乐观生效，失败时准确回滚。
- 删除需要确认，失败时任务和编辑上下文保留。
- 截止时间按账号 IANA 时区展示和编辑，提交为 UTC。
- 加载、空数据、错误、重试、禁用和键盘状态明确。
- Web 不持久化任务或 access token。
- 不实现数据导出、AI 或额外 API。
- 所有约定检查通过，文档只描述真实能力。
