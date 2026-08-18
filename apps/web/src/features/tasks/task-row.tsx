import { CalendarClock } from "lucide-react"

import type { Task, TaskPriority, TaskStatus } from "./task-api"
import { formatDueLabel, formatTaskTimestamp } from "./task-time"

type TaskRowProps = {
  task: Task
  timeZone: string
  statusMutating: boolean
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
  onSelect,
  onStatusChange,
}: TaskRowProps) {
  const priorityLabel =
    task.priority === null ? null : priorityLabels[task.priority]
  const completed = task.status === "completed"

  return (
    <article className="task-row" data-status={task.status}>
      <button
        type="button"
        className="task-row-main"
        aria-label={`编辑 ${task.title}`}
        onClick={() => onSelect(task)}
      >
        <span className="task-row-serial">#{task.serial}</span>
        <span
          className={
            completed ? "task-row-title line-through" : "task-row-title"
          }
        >
          {task.title}
        </span>
        <span className="task-topic">{task.topic}</span>
        <span className="task-row-meta">
          {priorityLabel !== null ? (
            <span data-priority={task.priority}>{priorityLabel}</span>
          ) : null}
          {task.due_at !== null ? (
            <span>
              <CalendarClock aria-hidden="true" />
              {formatDueLabel(task.due_at, timeZone)}
            </span>
          ) : null}
          <span>{formatTaskTimestamp(task.created_at, timeZone, "创建")}</span>
          {task.completed_at !== null ? (
            <span>
              {formatTaskTimestamp(task.completed_at, timeZone, "完成")}
            </span>
          ) : null}
        </span>
      </button>
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
    </article>
  )
}
