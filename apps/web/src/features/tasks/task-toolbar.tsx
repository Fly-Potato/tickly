import type { SortOrder, TaskSort, TaskStatus } from "./task-api"

type TaskToolbarProps = {
  status: TaskStatus
  sort: TaskSort
  order: SortOrder
  disabled: boolean
  onStatusChange(value: TaskStatus): void
  onSortChange(value: TaskSort): void
  onOrderChange(value: SortOrder): void
}

const statuses: Array<{ value: TaskStatus; label: string }> = [
  { value: "all", label: "全部" },
  { value: "active", label: "进行中" },
  { value: "completed", label: "已完成" },
]

export function TaskToolbar({
  status,
  sort,
  order,
  disabled,
  onStatusChange,
  onSortChange,
  onOrderChange,
}: TaskToolbarProps) {
  return (
    <div className="task-toolbar" aria-label="任务筛选与排序">
      <div className="task-status-tabs">
        {statuses.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={status === item.value}
            disabled={disabled}
            onClick={() => onStatusChange(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="task-sort-controls">
        <label>
          <span>排序</span>
          <select
            value={sort}
            disabled={disabled}
            onChange={(event) => onSortChange(event.target.value as TaskSort)}
          >
            <option value="created_at">创建时间</option>
            <option value="due_at">截止时间</option>
            <option value="priority">优先级</option>
          </select>
        </label>
        <label>
          <span>顺序</span>
          <select
            value={order}
            disabled={disabled}
            onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          >
            <option value="desc">降序</option>
            <option value="asc">升序</option>
          </select>
        </label>
      </div>
    </div>
  )
}
