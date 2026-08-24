# Todo 树分页与结构变更联动

作用域：
- `apps/api`
- `apps/web`

适用：
- 修改父子待办状态、`useTaskWorkspace`、父待办搜索、子待办创建、主题编辑或根任务 cursor 分页时。

结论：
- 列表分页单位是根 `TaskGroup`。从后续页打开父任务并创建子待办后，刷新第一页若不含该父任务，必须保留已更新的父分组和选中上下文；第一页已返回父分组时以服务端结果为准。
- 分组筛选规则是：根任务匹配时展示其全部直接子任务；根任务不匹配时只展示匹配的子任务；`child_count`、`completed_child_count` 和 `resolved_child_count` 始终基于完整直接子任务集合，其中已处理数包含 `completed` 与 `cancelled`。
- 父任务进入 `cancelled` 时，API 在同一事务内只级联当前账号的 `new` / `in_progress` 直接子任务；已完成和已废弃子任务不变，恢复父任务也不恢复子任务。Web 可局部维护单个子任务的计数，但父任务废弃后不猜测级联结果，必须重读服务端分组。
- `create`、`save`、`remove` 共用同步结构变更互斥，避免后发刷新覆盖分页父分组或产生父删除与子创建竞态；状态切换保持独立。
- 父候选 cursor 与搜索词绑定。输入变化必须在 250ms 查询等待前立即取消旧请求并失效旧 cursor；加载更多还需同步锁防止同一渲染周期重复请求。
- 主题列表是服务端派生数据。创建、删除以及包含 `topic` 的保存成功后需要刷新；主题刷新失败只进入独立 `topicError`，不能把已成功的任务写入误报为失败。

联动：
- 修改上述行为时同步检查 API `test_tasks_service.py`、`test_tasks_api.py` 与 Web `use-task-workspace.test.tsx`、`task-editor-panel.test.tsx`、`todo-workspace.test.tsx` 中的事务级联、完整计数、分页、竞态、筛选与错误域用例。

证据：
- `apps/api/app/services/tasks.py`
- `apps/api/tests/test_tasks_service.py`
- `apps/web/src/features/tasks/use-task-workspace.ts`
- `apps/web/src/features/tasks/parent-task-picker.tsx`
- `apps/web/src/features/tasks/child-task-create-form.tsx`
- `apps/web/src/features/tasks/use-task-workspace.test.tsx`
