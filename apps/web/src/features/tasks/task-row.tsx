import { CalendarClock } from "lucide-react"

import type { Task } from "./task-api"
import { formatDueLabel } from "./task-time"

type TaskRowProps = {
  task: Task
  timeZone: string
  completing: boolean
  onSelect(task: Task): void
  onCompletedChange(task: Task, completed: boolean): Promise<void>
}

const priorityLabels = {
  none: "",
  low: "低优先级",
  medium: "中优先级",
  high: "高优先级",
} as const

export function TaskRow({
  task,
  timeZone,
  completing,
  onSelect,
  onCompletedChange,
}: TaskRowProps) {
  const checkboxLabel = task.is_completed
    ? `将${task.title}标记为进行中`
    : `将${task.title}标记为已完成`
  const priorityLabel = priorityLabels[task.priority]

  return (
    <article className="task-row" data-completed={task.is_completed || undefined}>
      <input
        type="checkbox"
        className="task-checkbox"
        aria-label={checkboxLabel}
        checked={task.is_completed}
        disabled={completing}
        onChange={(event) => {
          void onCompletedChange(task, event.target.checked).catch(() => undefined)
        }}
      />
      <button
        type="button"
        className="task-row-main"
        aria-label={`编辑 ${task.title}`}
        onClick={() => onSelect(task)}
      >
        <span className="task-row-title">{task.title}</span>
        <span className="task-row-meta">
          {priorityLabel !== "" ? (
            <span data-priority={task.priority}>{priorityLabel}</span>
          ) : null}
          {task.due_at !== null ? (
            <span>
              <CalendarClock aria-hidden="true" />
              {formatDueLabel(task.due_at, timeZone)}
            </span>
          ) : null}
          {task.is_completed ? <span>已完成</span> : null}
        </span>
      </button>
    </article>
  )
}
