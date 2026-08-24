# Tickly 待办废弃状态设计

## 背景

Tickly 当前在 API、Web 和 MCP 中共同使用 `new`、`in_progress`、
`completed` 三种任务状态。数据库通过检查约束限制状态值，任务 service 维护
`completed_at`，Web 将状态写入 URL 筛选参数，MCP 通过
`set_task_status` 暴露同一状态契约。

本次新增可恢复的第四状态 `cancelled`，用户界面显示为 `Cancelled` 或
“已废弃”。废弃是任务生命周期状态，不替代现有硬删除，也不建立独立归档系统。

## 目标

- API、Web 和 MCP 统一支持创建后的任务切换到 `cancelled`。
- “全部”筛选继续包含全部四种状态，并提供独立的废弃状态筛选。
- 废弃父任务时，原子级联废弃尚未完成的直接子任务。
- 完成和废弃的直接子任务共同计入父任务的“已处理”进度。
- AI 操作父任务废弃前明确说明影响范围，取得确认后再执行并核验。
- 通过 migration、服务、HTTP、MCP、Web 和 Skill eval 覆盖新增契约。

## 非目标

- 不新增 `cancelled_at`、废弃原因或状态历史表。
- 不记录子任务废弃前状态，也不在恢复父任务时自动恢复子任务。
- 不把废弃实现为软删除、归档表或默认隐藏能力。
- 不改变硬删除、父子层级、账号所有权或 MCP 无删除工具的边界。
- 不回写已经完成的历史设计文档。

## 已确认决策

| 项目 | 决策 |
| --- | --- |
| 协议值 | `cancelled` |
| 展示文案 | `Cancelled` / “已废弃” |
| 是否可恢复 | 可切换回 `new`、`in_progress` 或 `completed` |
| “全部”筛选 | 包含废弃任务 |
| 废弃时间 | 不新增字段 |
| `completed_at` | 仅 `completed` 非空；进入 `cancelled` 时清空 |
| 父任务废弃 | 级联直接子任务中的 `new` 和 `in_progress` |
| 已完成子任务 | 保持 `completed`，时间和 `updated_at` 均不修改 |
| 已废弃子任务 | 保持 `cancelled`，`updated_at` 不修改 |
| 父任务恢复 | 不自动恢复任何子任务 |
| 子任务进度 | `completed` 与 `cancelled` 共同计入“已处理” |
| AI 操作 | 执行前列明影响并确认，执行后重新读取核验 |

## 状态与时间不变量

任务状态集合扩展为：

```text
new | in_progress | completed | cancelled
```

状态可以在四个值之间切换，不增加不可逆限制。`completed_at` 继续只表达完成
时间，不承担通用终止时间语义：

- 从非 `completed` 进入 `completed` 时写入当前 UTC 时间。
- 重复提交 `completed` 时保留原完成时间。
- 进入 `new`、`in_progress` 或 `cancelled` 时清空 `completed_at`。
- 从 `cancelled` 进入 `completed` 时生成新的完成时间。
- 重复提交 `cancelled` 时父任务仍需重新检查直接子任务，确保当前仍为
  `new` 或 `in_progress` 的子任务被级联废弃。

## 数据库迁移

新增 `0003` Alembic revision，仅扩展 `ck_tasks_status`：

```sql
status IN ('new', 'in_progress', 'completed', 'cancelled')
```

SQLite 使用 Alembic batch 重建任务表，保留现有列、数据、索引、唯一约束和
父任务外键。升级前检查现有状态均属于旧三状态，避免非事务 DDL 中途失败。
升级不需要数据回填。

降级有损：先把 `cancelled` 更新为 `new`，再恢复旧三状态检查约束。降级不得
把废弃任务映射为 `completed`，也不得生成 `completed_at`。

## API 与事务

`TaskStatus` 和 `TaskStatusFilter` 增加 `CANCELLED`。公开 Todo API 与内部 MCP
API 继续复用同一任务 service，列表筛选、cursor 条件绑定和响应校验均接受
`cancelled`。

状态更新仍由现有 PATCH 入口完成。当目标状态是 `cancelled` 时：

1. 在当前用户所有权约束下读取目标任务。
2. 如果目标任务拥有直接子任务，查询同账号、同父 ID 且状态为 `new` 或
   `in_progress` 的子任务。
3. 使用同一个 `now` 把目标任务和命中的子任务改为 `cancelled`，清空它们的
   `completed_at`，并更新命中记录的 `updated_at`。
4. `completed` 和已经 `cancelled` 的子任务完全不写入。
5. 父任务与子任务修改在同一事务提交；任何查询、写入或提交失败都整体回滚。

恢复父任务或修改普通子任务状态时不执行反向级联。父子状态仍允许独立修改；
如果废弃父任务保持不变但某个子任务后来恢复为未完成状态，再次提交父任务
`cancelled` 会重新将该子任务废弃。

## 子任务统计契约

现有 `child_count` 和 `completed_child_count` 保持原语义，避免静默破坏已有
消费者。任务组响应新增：

```text
resolved_child_count = completed 子任务数 + cancelled 子任务数
```

该值始终针对完整直接子任务集合计算，不受当前状态、主题或关键词筛选导致的
子任务裁剪影响。API、内部 MCP 响应、MCP 输出模型和 Web 类型同步增加该字段。

## Web 行为

- `TaskStatus`、`TaskStatusFilter` 和 URL 参数解析接受 `cancelled`。
- 桌面筛选、移动筛选、行内状态选择和编辑面板增加废弃选项。
- “全部”继续请求 `status=all`；废弃筛选请求 `status=cancelled`。
- 废弃筛选的空状态文案为“还没有已废弃的任务。”。
- 行标题使用与完成不同但明确的弱化样式；状态选择器继续显示真实状态，不把
  废弃伪装为删除或完成。
- 父任务进度改用 `resolved_child_count / child_count`，文案改为“已处理”。
- 状态写入继续采用现有单任务 mutation 锁、乐观状态和失败回滚。父任务级联
  结果不在客户端猜测；服务端响应后重读当前 query，以服务端任务树为准。
- 仅 `completed` 且 `completed_at` 非空时展示完成时间；废弃任务不展示伪造的
  完成或废弃时间。

当任务在特定状态筛选中切换到其他状态，刷新后按服务端筛选结果移出当前列表；
写入失败时保留现有局部错误提示和精确回滚行为。

## MCP 与 AI Skill

MCP 的 `TaskStatusValue`、`TaskStatusFilter`、`TaskStatus` 和所有任务输出模型增加
`cancelled`。`list_tasks(status="cancelled")` 返回废弃任务；
`set_task_status` 继续只调用内部 API，不在 MCP 层复制级联事务。

`skills/tickly-todo-mcp/SKILL.md` 增加以下约束：

- 用户要求废弃单个无子任务任务时，明确说明目标 serial 后按普通写操作确认。
- 用户要求废弃父任务时，先用 `get_task` 读取父任务与全部直接子任务。
- 执行前列出父任务 serial、会级联废弃的 `new` / `in_progress` 子任务 serial、
  保持不变的 `completed` 子任务 serial，并说明恢复父任务不会恢复子任务。
- 用户明确确认后才调用 `set_task_status(status="cancelled")`。
- 写入后再次调用 `get_task`，按实际状态分别报告父任务、已级联废弃子任务、
  保持完成的子任务和失败项；不得仅凭预期宣称级联成功。
- 批量废弃多个父任务时继续遵守完整分页、serial 去重和一次范围确认规则。

Skill eval 增加父任务级联提示、用户拒绝确认不写入，以及恢复父任务不恢复
子任务的场景。MCP 仍不提供删除工具，Skill 不得把 `cancelled` 描述成删除。

## 错误、并发与安全边界

- 级联只操作当前用户拥有且 `parent_id` 等于目标父任务 ID 的直接子任务。
- 一层父子不变量保持不变，不递归扫描后代。
- API 事务失败不得留下父任务已废弃、部分子任务未废弃的状态。
- Web 不根据当前可见子任务自行推导完整级联集合，因为状态筛选可能裁剪子任务。
- MCP 和 Skill 不访问 SQLite，不绕过 API，也不让模型提供数据库 ID 或用户 ID。
- 已经在执行的同任务 Web 状态 mutation 继续被前端互斥；不同任务的并发最终由
  服务端事务提交顺序决定，后提交的明确用户操作可以成为最终状态。

## 测试与验证

### API

- migration 升级保留现有数据并允许 `cancelled`，降级把它映射为 `new`。
- schema、模型检查约束、HTTP 请求和响应接受第四状态并拒绝其他值。
- 四状态筛选、cursor 条件绑定和关键词/主题 AND 语义保持正确。
- 父任务废弃原子级联 `new` / `in_progress` 子任务。
- `completed` / `cancelled` 子任务不被写入，恢复父任务不恢复子任务。
- `completed_at` 写入、保留、清空与重新完成符合不变量。
- `resolved_child_count` 基于完整直接子任务集合计算。
- 模拟提交失败时父子修改全部回滚。

### MCP

- 工具 JSON Schema、输入校验、内部 API 请求和输出模型支持 `cancelled`。
- `list_tasks` 传递废弃筛选，`set_task_status` 传递第四状态。
- 任务组输出包含 `resolved_child_count`，非法状态仍返回稳定协议错误。

### Web

- API 类型、URL 恢复、桌面和移动筛选支持废弃状态。
- 行内和编辑面板可以提交废弃及恢复操作。
- 废弃筛选空状态、废弃视觉状态和“已处理”统计正确。
- 父任务废弃后以服务端刷新结果更新子任务；失败继续精确回滚。
- 状态筛选切换、分页、并发保存和主题错误域不回归。

### Skill 与文档

- Skill eval 验证级联前提示、确认门和执行后核验。
- Skill 结构校验与 `git diff --check` 通过。
- 更新当前事实文档 `AGENTS.md`、`README.md` 和 `docs/mcp.md`；历史 specs、
  plans 与 roadmap 保持不变。

实施完成后至少运行：

```text
mise exec -- pnpm test:api
mise exec -- pnpm test:mcp
mise exec -- pnpm test:web
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
```

## 验收标准

- 四个状态可通过 Web、公开 API 和 MCP 读取、筛选与切换。
- 废弃父任务时，父任务与所有未完成直接子任务在同一事务变为 `cancelled`，
  已完成子任务保持不变。
- 恢复父任务不会恢复子任务。
- “全部”包含废弃任务，“已废弃”筛选只显示匹配任务组。
- 父任务进度以完成加废弃的子任务数展示为“已处理”。
- AI 在级联写入前给出明确影响提示并取得确认，写入后依据真实读取结果报告。
- 没有新增废弃时间、状态历史、归档或 MCP 删除能力。
