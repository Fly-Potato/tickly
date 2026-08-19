import type { Task, TaskPriority, TaskStatus } from "./task-api"
import { formatDueLabel } from "./task-time"

type TaskRowProps = {
  task: Task
  timeZone: string
  statusMutating: boolean
  child?: boolean
  progress?: { completed: number; total: number }
  contextOnly?: boolean
  onSelect(task: Task): void
  onStatusChange(task: Task, status: TaskStatus): Promise<void>
}

const priorityLabels: Record<TaskPriority, string> = {
  low: "低优先级",
  medium: "中优先级",
  high: "高优先级",
}

const statusOptions: ReadonlyArray<{ value: TaskStatus; label: string }> = [
  { value: "new", label: "New" },
  { value: "in_progress", label: "In Progress" },
  { value: "completed", label: "Completed" },
]

export function TaskRow({
  task,
  timeZone,
  statusMutating,
  child = false,
  progress,
  contextOnly = false,
  onSelect,
  onStatusChange,
}: TaskRowProps) {
  const priorityLabel =
    task.priority === null ? null : priorityLabels[task.priority]
  const completed = task.status === "completed"

  return (
    <tr
      className="task-row"
      data-status={task.status}
      data-child={child || undefined}
    >
      <td className="task-row-serial">#{task.serial}</td>
      <td className="task-row-task">
        <button
          type="button"
          className="task-row-main"
          aria-label={`编辑 ${task.title}`}
          onClick={() => onSelect(task)}
        >
          <span
            className={
              completed ? "task-row-title line-through" : "task-row-title"
            }
          >
            {task.title}
          </span>
          {progress !== undefined ? (
            <span className="task-row-progress">
              {progress.completed}/{progress.total} 已完成
            </span>
          ) : null}
          {contextOnly ? (
            <span className="task-context-note">仅用于展示匹配的子待办</span>
          ) : null}
        </button>
      </td>
      <td className="task-row-topic">{task.topic}</td>
      <td
        className="task-row-priority"
        data-priority={task.priority ?? undefined}
      >
        {priorityLabel ?? "—"}
      </td>
      <td className="task-row-due">
        {task.due_at === null ? "—" : formatDueLabel(task.due_at, timeZone)}
      </td>
      <td className="task-row-status">
        <select
          aria-label={`设置 #${task.serial} 的状态`}
          value={task.status}
          disabled={statusMutating}
          onChange={(event) => {
            void onStatusChange(task, event.target.value as TaskStatus).catch(
              () => undefined
            )
          }}
        >
          {statusOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </td>
    </tr>
  )
}
