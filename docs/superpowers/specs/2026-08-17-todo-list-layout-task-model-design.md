# Tickly Todo List 两栏布局与任务模型设计

## 1. 目标

把现有 Todo 单列工作区调整为“左侧筛选、右侧 Todo List”的响应式两栏布局，同时把单条任务升级为具有用户可见流水编号、三阶段状态、必填主题和一层父子关系的稳定业务模型。

本设计完成后，用户可以按状态和主题筛选任务，在右侧以父子树查看 Todo，并继续使用快速创建、编辑、完成、取消完成、删除、排序和 cursor 分页能力。

## 2. 当前基础

当前 Web 已提供：

- `TodoWorkspace` 单列工作区。
- 标题快速创建。
- All、Active、Completed 状态筛选。
- 创建时间、截止时间和优先级排序。
- cursor 分页、任务编辑、完成乐观更新和删除确认。
- 桌面右侧编辑面板和移动端底部抽屉。

当前 API 任务字段为：

```text
id
title
notes
is_completed
priority
due_at
completed_at
created_at
updated_at
```

当前模型没有用户可见编号、主题或父子关系，完成状态只使用布尔值表达，`priority=none` 用枚举值模拟空优先级。

## 3. 已确认的产品决策

- 页面在桌面端采用两栏布局：左侧筛选，右侧 Todo List。
- 移动端不保留固定左栏，筛选改为抽屉。
- 截止时间 `due_at` 保留，但不是必填项。
- 描述创建时省略或为空，则使用当时的标题作为默认值；之后标题和描述独立修改。
- 状态使用 `new`、`in_progress`、`completed`。
- 主题为必填自由文本，不建立独立主题实体或管理页面。
- 父子关系只支持一层。
- 父待办与子待办的状态相互独立，不自动级联。
- 用户可见编号使用字段名 `serial`，按账号独立递增，删除后不复用。

## 4. 范围

### 4.1 包含

- Todo 数据库 migration 和旧数据回填。
- SQLAlchemy 模型、Pydantic schema、service 和 API 契约调整。
- 账号内任务流水编号分配。
- 三阶段状态和服务端完成时间维护。
- 必填自由文本主题及主题筛选选项接口。
- 一层父子关系、层级校验和树形列表响应。
- Web 两栏布局、移动筛选抽屉和父子任务展示。
- 快速创建、编辑面板、筛选、排序和分页适配。
- API 与 Web 行为测试。

### 4.2 不包含

- 独立项目、主题或标签管理页面。
- 多主题、标签颜色、图标或主题权限。
- 超过一层的任务树。
- 父子状态自动完成、自动回退或级联删除。
- 拖拽排序、手工排序和跨父级拖拽。
- 批量操作、搜索、提醒、重复任务或 AI 能力。
- 新的全局状态库、路由体系或跨 workspace 包。

## 5. 方案比较

### 5.1 正式升级数据模型并同步调整 Web（采用）

通过新 migration 持久化 `serial`、`description`、`topic`、`status` 和 `parent_id`，API 与 Web 使用同一契约。

优点：

- 页面刷新和跨设备读取的语义一致。
- 筛选、分页和父子关系由服务端保证。
- 可以在数据库、service 和 API 层完整校验不变量。

代价：

- 需要同时修改 API、migration、Web 和测试。

### 5.2 只在 Web 推导新字段（不采用）

在浏览器内根据列表位置产生编号，并从备注中临时解析主题或父子关系。

该方案无法跨分页稳定编号，也不能保证多设备一致；刷新后关系可能变化，不满足持久化任务模型要求。

### 5.3 完整主题实体和无限层级任务树（不采用）

为主题建立独立表和 CRUD，并使用任意深度递归树。

该方案需要主题生命周期、递归查询、循环检测、树分页和复杂移动规则。当前只有必填自由文本主题和一层子待办需求，不提前引入这套复杂度。

## 6. 页面布局

### 6.1 桌面端

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ [T] Tickly                                  potato · Asia/Shanghai   [退出]  │
├───────────────────┬──────────────────────────────────────────────────────────┤
│ 筛选              │ TODO LIST                                                │
│                   │                                                          │
│ 状态              │ 新建待办                                                 │
│ ○ 全部            │ [标题________________] [主题____________] [添加]         │
│ ○ New             │                                                          │
│ ○ In Progress     │ 当前：In Progress / Tickly              排序：创建时间  │
│ ○ Completed       │                                                          │
│                   │ ┌──────────────────────────────────────────────────────┐ │
│ 主题              │ │ □ #18 调整 Todo List 页面布局                     │ │
│ ○ 全部主题        │ │   Tickly · 高优先级 · In Progress                   │ │
│ ○ Tickly          │ │   创建于 2026-08-17 · 截止于 2026-08-20             │ │
│ ○ 工作            │ │                                                      │ │
│ ○ 学习            │ │   子待办 1/2 已完成                                 │ │
│                   │ │   ├─ ✓ #19 确认字段结构            Completed         │ │
│ 排序              │ │   └─ □ #20 调整响应式布局          New               │ │
│ [创建时间 ▼]      │ └──────────────────────────────────────────────────────┘ │
│ [降序 ▼]          │                                                          │
│                   │ ┌──────────────────────────────────────────────────────┐ │
│ [清除筛选]        │ │ □ #21 编写 API migration                            │ │
│                   │ │   Tickly · 无优先级 · New                            │ │
│                   │ └──────────────────────────────────────────────────────┘ │
│                   │                         [加载更多]                       │
└───────────────────┴──────────────────────────────────────────────────────────┘
```

布局规则：

- 桌面左栏宽度控制在 `240px` 至 `280px`，右栏使用剩余空间。
- 页面头部继续横跨整个工作区，不放入筛选栏。
- 快速创建、当前筛选摘要、任务树和加载更多都属于右栏。
- 左栏承载状态、主题、排序和清除筛选操作。
- 左栏在页面滚动时保持可见，但不创建独立滚动容器。
- 点击任务继续打开现有右侧 Dialog 编辑面板；编辑面板是覆盖层，不把主页面变成三栏。

### 6.2 移动端

```text
┌────────────────────────────┐
│ Tickly              [账号] │
├────────────────────────────┤
│ TODO LIST          [筛选]  │
│                            │
│ [标题____________________] │
│ [主题____________] [添加] │
│                            │
│ In Progress · Tickly   [×] │
│                            │
│ □ #18 调整页面布局         │
│   Tickly · In Progress     │
│   子待办 1/2               │
│   ├─ ✓ #19 确认字段        │
│   └─ □ #20 响应式布局      │
│                            │
│       [加载更多]           │
└────────────────────────────┘
```

- 固定左栏改为“筛选”按钮打开的底部抽屉。
- 抽屉包含与桌面相同的状态、主题和排序字段。
- 抽屉内修改先保存在本地草稿，点击“应用”后统一更新 query，避免每次选择都触发重新加载。
- 生效的状态和主题在列表上方显示为可单独移除的筛选摘要。
- 任务编辑仍使用现有移动端底部编辑抽屉。

## 7. 任务数据模型

### 7.1 对外任务字段

```text
Task:
  id: UUID string
  serial: positive integer
  title: string
  description: string
  priority: low | medium | high | null
  topic: string
  status: new | in_progress | completed
  due_at: UTC datetime | null
  completed_at: UTC datetime | null
  parent_id: UUID string | null
  created_at: UTC datetime
  updated_at: UTC datetime
```

字段规则：

| 字段 | 规则 |
| --- | --- |
| `id` | 内部主键和 API 关联标识，不直接作为用户编号展示。 |
| `serial` | 账号内从 1 递增，创建后不可修改，删除后不复用。 |
| `title` | 必填，去除首尾空白，长度 1 至 200。 |
| `description` | 存储后始终非空，长度 1 至 4000。创建时省略、`null` 或只有空白字符则复制规范化后的标题。更新后不能清空。 |
| `priority` | 可空；非空值只能为 `low`、`medium`、`high`。 |
| `topic` | 必填自由文本，去除首尾空白，长度 1 至 100；大小写不自动合并。 |
| `status` | 默认 `new`，只允许三个已确认值。 |
| `due_at` | 可空截止时间，外部输入必须包含时区并在服务端转换为 UTC。 |
| `completed_at` | 只由 service 根据状态转换维护，客户端不可直接写入。 |
| `parent_id` | 可空；非空时必须引用当前用户的顶层任务。 |
| `created_at` | 服务端创建时写入，不允许客户端指定。 |
| `updated_at` | 每次真实修改时由服务端写入，不允许客户端指定。 |

`children` 是列表响应根据 `parent_id` 组装的直接子待办集合，不作为 JSON 或重复 ID 数组存储在任务表中。

### 7.2 数据库约束与索引

- `UNIQUE(user_id, serial)` 保证账号内编号唯一。
- `serial > 0`。
- `status IN ('new', 'in_progress', 'completed')`。
- `priority IS NULL OR priority IN ('low', 'medium', 'high')`。
- 标题、描述和主题保留数据库长度约束。
- `parent_id` 自引用 `tasks.id`，删除父任务时使用 `ON DELETE SET NULL`。
- 新增 `(user_id, status)`、`(user_id, topic)` 和 `(user_id, parent_id)` 索引。
- 保留截止时间和创建时间排序索引，并根据新列表查询调整复合索引顺序。
- 同用户和一层关系无法只靠普通外键表达，必须由 service 在写事务内校验。

### 7.3 流水编号分配

账号记录增加仅供服务端使用的 `next_task_serial` 计数器：

1. 创建任务的事务原子取得并递增当前账号计数器。
2. 取得的旧值写入新任务 `serial`。
3. 任务创建失败时整个事务回滚，计数器也回滚。
4. 删除任务不回退计数器。
5. Web 不提交 `serial`，API 也不提供修改入口。

该方式避免使用 `MAX(serial) + 1` 造成并发创建重复编号。

## 8. 状态与时间语义

```text
new ──────────> in_progress ──────────> completed
 │                    │                      │
 └────────────────────┴──────────────────────┘
                 允许显式切换
```

- 创建任务固定为 `new`，创建请求不接受自定义状态。
- 从非完成状态首次进入 `completed` 时，写入当前 UTC 时间。
- 已经是 `completed` 时重复提交相同状态，保留原 `completed_at`。
- 从 `completed` 进入 `new` 或 `in_progress` 时，清空 `completed_at`。
- 重新进入 `completed` 时写入新的完成时间。
- 修改标题、描述、主题、优先级、截止时间或父级不改变当前状态。
- 截止时间和完成时间是两个独立字段；任务可以没有截止时间，也可以在截止时间前后完成。

Web 不再通过复选框表达所有状态。每个任务行使用带可访问名称的紧凑状态选择框，显式提供 `New`、`In Progress` 和 `Completed` 三个选项；`completed` 仍使用完成图标和弱化样式，但不能把 `new` 与 `in_progress` 合并为同一种视觉状态。

## 9. 父子关系语义

### 9.1 写入规则

- 根任务的 `parent_id` 为 `null`。
- 子任务的父任务必须存在、属于同一用户且自身 `parent_id` 为 `null`。
- 禁止把任务设为自己的父任务。
- 已经拥有子任务的任务不能再成为子任务。
- 子任务可以移动到另一个合法根任务，也可以解除父级并成为根任务。
- 设置或修改父级时在同一个写事务内重新读取父任务并校验，不信任客户端传入的任务摘要。

### 9.2 状态规则

- 完成父任务不会自动修改子任务。
- 完成全部子任务不会自动修改父任务。
- 父任务显示 `已完成子任务数 / 子任务总数`，该进度只用于提示。
- 父任务和子任务分别维护自己的 `completed_at`。

### 9.3 删除规则

- 删除子任务只删除该任务。
- 删除父任务前由数据库外键把直接子任务的 `parent_id` 设为 `null`，子任务成为根任务。
- 删除确认必须提示存在多少子任务会被提升为根任务。
- 不提供级联删除子任务的隐式行为。

## 10. 列表、筛选与分页

### 10.1 筛选值

状态 query：

```text
all
new
in_progress
completed
```

其中 `all` 只表示不限制状态，不是任务持久化状态。

主题 query 使用去除首尾空白后的精确匹配。主题大小写保持用户输入，不把 `Tickly` 和 `tickly` 自动合并。

### 10.2 树筛选规则

- 根任务匹配筛选时，返回根任务及其全部直接子任务，以保留完整的子待办上下文。
- 根任务不匹配但一个或多个子任务匹配时，仍返回父任务作为上下文，但只返回匹配的子任务。
- `TaskGroupResponse.context_only=true` 表示父任务只是为了展示命中子任务而保留。
- 没有任何筛选时，返回所有根任务及其全部直接子任务。
- 子任务不会脱离父任务单独出现在主列表顶层。

### 10.3 排序和分页

支持排序：

```text
serial
created_at
due_at
priority
```

- 默认继续使用 `created_at desc`。
- 可空的 `due_at` 和 `priority` 在升降序中都固定放在末尾。
- 顶层分组按根任务字段排序。
- 分组内子任务按 `serial asc` 稳定排列。
- cursor 以根任务分组为分页单位，父任务和当前响应中的子任务不会被拆到不同页。
- 列表仍不返回 `total`，左侧筛选不显示不可靠的数量徽标。

## 11. API 契约

### 11.1 创建和更新

```text
POST /api/v1/tasks
```

创建请求：

```text
title: required
description: optional
priority: optional nullable
topic: required
due_at: optional nullable
parent_id: optional nullable
```

创建请求不接受 `id`、`serial`、`status`、`completed_at`、`created_at` 或 `updated_at`。

```text
PATCH /api/v1/tasks/{task_id}
```

允许修改：

```text
title
description
priority
topic
status
due_at
parent_id
```

响应保持平坦的 `TaskResponse`；父子集合只在列表或详情响应中组装。

### 11.2 树形列表

```text
GET /api/v1/tasks
  ?status=all|new|in_progress|completed
  &topic=<exact topic>
  &sort=serial|created_at|due_at|priority
  &order=asc|desc
  &cursor=<opaque cursor>
  &limit=<1..100>
```

列表响应：

```text
TaskListResponse:
  items: TaskGroupResponse[]
  next_cursor: string | null

TaskGroupResponse:
  task: TaskResponse
  children: TaskResponse[]
  child_count: integer
  completed_child_count: integer
  context_only: boolean
```

`children` 可以因当前筛选只包含命中的子待办；两个 count 字段始终基于该父任务的完整直接子待办集合，用于进度和删除提示。

列表查询始终绑定当前 `user_id`，父任务、子任务和主题选项均不能泄漏其他用户的数据。

### 11.3 详情和主题选项

```text
GET /api/v1/tasks/{task_id}
```

详情响应在平坦任务字段之外返回直接子任务；查询子任务详情时，子任务列表为空。

```text
GET /api/v1/tasks/topics
```

- 返回当前用户所有任务中去重后的主题字符串。
- 按不区分大小写的展示顺序排序，但不合并大小写不同的主题。
- 接口只用于筛选和输入建议，不代表主题具有独立生命周期。

父待办选择使用独立的轻量候选接口：

```text
GET /api/v1/tasks/parent-options
  ?query=<optional serial or title>
  &cursor=<opaque cursor>
  &limit=<1..100>
```

- 只返回当前用户的根任务摘要：`id`、`serial`、`title`、`topic` 和 `status`。
- 编辑任务时由 Web 排除任务自身；service 仍在最终写事务中校验自引用和层级。
- `query` 为纯数字或以 `#` 开头的数字时匹配 `serial`，其他文本按标题包含关系检索。
- 候选结果使用 cursor 分页，不能依赖当前主列表已加载的数据。

## 12. Web 组件边界

```text
TodoWorkspace
├── WorkspaceHeader
├── TodoWorkspaceLayout
│   ├── TaskFilterSidebar
│   │   ├── StatusFilter
│   │   ├── TopicFilter
│   │   └── TaskSortControls
│   └── TaskListContent
│       ├── QuickCreateForm
│       ├── ActiveFilterSummary
│       └── TaskTreeList
│           └── TaskGroup
│               ├── TaskRow
│               └── ChildTaskList
├── MobileTaskFilterDialog
├── TaskEditorPanel
└── DeleteTaskDialog
```

职责：

- `TodoWorkspace` 继续作为唯一业务容器，只协调选中任务、脏表单保护和组件回调。
- `useTaskWorkspace` 管理 query、主题选项、树分页和 mutation 状态，不提升到 Auth Context。
- `TaskFilterSidebar` 和移动筛选抽屉复用同一组纯筛选控件。
- `TaskTreeList` 只渲染服务端返回的分组，不在浏览器中重新猜测跨页父子关系。
- `TaskEditorPanel` 增加描述、主题、状态和父待办字段，并保留显式保存、删除确认和错误保留语义。
- 不创建新的全局 Store、通用请求框架或 `packages/*` 包。

## 13. 创建与编辑交互

### 13.1 快速创建

- 快速创建至少显示标题和主题。
- 当前选中了具体主题时，主题输入默认使用该主题，但仍允许修改。
- 当前为“全部主题”时，用户必须输入或从建议中选择主题。
- 快速创建不发送描述，服务端使用标题填充。
- 新任务固定为根任务和 `new` 状态。
- 成功后重新加载当前 query，确保任务只在符合筛选时出现。

### 13.2 创建子待办

- 父任务的编辑面板提供“添加子待办”。
- 创建子待办至少输入标题，默认继承父任务主题并允许修改。
- 请求显式提交父任务 `parent_id`，服务端重新校验层级和所有权。
- 创建成功后刷新当前父任务分组和子任务进度。

### 13.3 编辑

- 编辑面板继续使用打开任务的独立表单快照和最小 PATCH。
- `serial`、创建时间和完成时间只读。
- 描述更新后不能清空；清空时显示字段错误且不提交。
- 父待办选择器通过候选接口检索当前账号下的根任务，支持按 `serial` 或标题继续查找；服务端仍执行最终校验。
- 保存导致任务不再匹配当前筛选时，从列表移除任务或分组，并保留明确的成功反馈。

## 14. Migration

新增一份 Alembic migration，不修改已经应用的初始 migration。

迁移规则：

1. 按每个用户的 `created_at asc, id asc` 为现有任务分配从 1 开始的 `serial`。
2. 为每个用户设置 `next_task_serial = max(serial) + 1`；没有任务的用户设置为 1。
3. 把 `notes` 迁移为 `description`；`null`、空字符串或只有空白的值回填为标题。
4. 把 `priority='none'` 迁移为 `null`。
5. 把 `is_completed=false` 迁移为 `status='new'`。
6. 把 `is_completed=true` 迁移为 `status='completed'`。
7. 已完成但缺少 `completed_at` 的历史任务使用 `updated_at` 作为可获得的最接近完成时间，并在 migration 注释中说明这是历史回填值。
8. 现有任务的 `topic` 回填为“未分类”；新建和更新接口不自动使用该值。
9. 现有任务的 `parent_id` 为 `null`。
10. 完成回填后建立非空约束、检查约束、外键和索引。
11. 删除旧的 `notes`、`is_completed` 列和不再适用的索引。

降级 migration 需要明确有损边界：三状态回退为布尔值时，只有 `completed` 映射为 `true`；`new` 和 `in_progress` 都映射为 `false`。主题、父子关系和流水编号在降级后不可保留。

## 15. 错误、加载与可访问性

| 场景 | 行为 |
| --- | --- |
| 列表首次加载 | 右栏显示任务树骨架，筛选控件暂时禁用。 |
| 主题选项失败 | 状态筛选和列表仍可使用；主题区显示局部错误和重试。 |
| 当前筛选无结果 | 显示包含当前状态和主题的空状态。 |
| 加载更多失败 | 保留已加载分组和 cursor，列表底部提供重试。 |
| 非法父级 | 编辑面板保留输入，显示服务端返回的稳定业务错误。 |
| 保存后不再匹配筛选 | 从当前结果移除，并通过 `aria-live` 说明任务已保存。 |
| 删除含子任务的父任务 | 确认框说明子任务数量及其会成为根任务。 |
| 状态更新失败 | 恢复原状态和完成时间，不影响父任务或其他子任务。 |

- 状态不能只用颜色表达，同时显示文字或图标。
- 状态控件、筛选按钮、父子展开、加载更多和编辑入口支持键盘。
- 父任务使用语义列表包含子任务列表，辅助技术可以识别层级。
- 移动筛选和编辑继续使用 Base UI Dialog 的焦点锁定、Escape 和焦点恢复能力。
- `prefers-reduced-motion` 下关闭非必要过渡。

## 16. 测试策略

### 16.1 API 与 migration

覆盖：

- 旧任务的流水编号、描述、优先级、状态、完成时间和主题回填。
- 每个账号独立从 1 分配编号，删除后不复用。
- 并发或相邻事务创建不会获得重复编号。
- 创建时描述默认值只取创建时标题，之后标题修改不联动描述。
- 主题必填、去除首尾空白、长度边界和大小写保留。
- 三状态切换及 `completed_at` 写入、保留、清空和重新写入。
- 父任务所有权、一层限制、自引用、已有子任务再成为子任务等非法关系。
- 删除父任务后子任务成为根任务。
- 树筛选命中父任务、命中子任务、上下文父任务和跨用户隔离。
- root group cursor 不拆分父子关系。
- `serial`、`completed_at` 和时间戳不能由客户端写入。
- OpenAPI 不暴露账号计数器或其他内部字段。

### 16.2 Web

覆盖：

- 桌面语义结构包含筛选栏和列表主区域。
- 移动筛选抽屉使用草稿，点击应用后只触发一次 query 更新。
- 状态、主题、排序和清除筛选。
- 任务行的三状态选择框及失败回滚。
- 具体主题会预填快速创建，全部主题下主题必填。
- 任务编号、主题、三种状态、截止时间和完成时间展示。
- 父子任务层级、子任务进度和上下文父任务。
- 父子状态相互独立。
- 创建子任务、重新指定父级和解除父级。
- 父待办候选按编号或标题检索、cursor 加载和跨用户隔离。
- 编辑描述不能清空，标题修改不自动修改描述。
- 含子任务父任务的删除提示。
- 加载、空数据、错误、重试、禁用和键盘交互。

jsdom 不断言像素宽度或 CSS media query 的实际布局；自动化测试验证语义区域和交互，响应式视觉由人工验收确认。

## 17. 实施顺序

1. 实现并验证 Alembic migration 和旧数据回填。
2. 调整 ORM、schema、service、route 和 OpenAPI 契约。
3. 完成 API 单元、service、接口和 migration 测试。
4. 调整 Web DTO、API wrapper 和 `useTaskWorkspace` 树分页状态。
5. 实现筛选侧栏、移动抽屉和右侧任务树布局。
6. 调整快速创建、编辑面板、状态操作和父子任务交互。
7. 完成 Web 行为测试和响应式样式。
8. 执行影响范围内的完整验证。

## 18. 验证

实施完成后从仓库根目录执行：

```powershell
mise exec -- pnpm test:api
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
mise exec -- pnpm test:web
git diff --check
git status --short
```

## 19. 验收标准

- 桌面端明确呈现左侧筛选和右侧 Todo List，两栏不会被编辑面板永久挤压成三栏。
- 移动端使用筛选抽屉，列表保持单列可读。
- 每个任务具有不可变、账号内唯一的用户可见 `serial`。
- 描述创建时可以由标题补齐，之后不随标题变化且不能被清空。
- 主题为必填自由文本，并可以在左侧精确筛选。
- 状态明确区分 `new`、`in_progress` 和 `completed`。
- 截止时间可空，完成时间只表示实际标记完成的时刻。
- 父子关系只允许一层，父子状态互不联动。
- 删除父任务不会隐式删除子任务。
- 筛选命中子任务时保留父任务上下文，cursor 不拆散任务分组。
- 所有读取和写入继续受当前用户所有权保护。
- 不实现独立主题管理、无限树、拖拽、批量操作、搜索、提醒或 AI。
