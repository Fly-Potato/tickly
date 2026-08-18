import type { ReactNode } from "react"

import type { SortOrder, TaskSort, TaskStatusFilter } from "./task-api"
import type { WorkspaceQuery } from "./use-task-workspace"

const STATUS_OPTIONS: ReadonlyArray<{
  value: TaskStatusFilter
  label: string
}> = [
  { value: "all", label: "全部" },
  { value: "new", label: "New" },
  { value: "in_progress", label: "In Progress" },
  { value: "completed", label: "Completed" },
]

const SORT_OPTIONS: ReadonlyArray<{ value: TaskSort; label: string }> = [
  { value: "serial", label: "流水号" },
  { value: "created_at", label: "创建时间" },
  { value: "due_at", label: "截止时间" },
  { value: "priority", label: "优先级" },
]

const ORDER_OPTIONS: ReadonlyArray<{ value: SortOrder; label: string }> = [
  { value: "asc", label: "升序" },
  { value: "desc", label: "降序" },
]

export type TaskFilterControlsProps = {
  query: WorkspaceQuery
  topics: string[]
  disabled: boolean
  topicDisabled?: boolean
  topicFeedback?: ReactNode
  onStatusChange(status: TaskStatusFilter): void
  onTopicChange(topic: string | undefined): void
  onSortChange(sort: TaskSort): void
  onOrderChange(order: SortOrder): void
}

export function TaskFilterControls({
  query,
  topics,
  disabled,
  topicDisabled = false,
  topicFeedback,
  onStatusChange,
  onTopicChange,
  onSortChange,
  onOrderChange,
}: TaskFilterControlsProps) {
  const topicControlsDisabled = disabled || topicDisabled

  return (
    <div className="task-filter-controls">
      <fieldset className="task-filter-section" disabled={disabled}>
        <legend className="text-sm font-semibold">状态</legend>
        <div className="task-filter-options">
          {STATUS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={disabled}
              aria-pressed={query.status === option.value}
              onClick={() => onStatusChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset
        className="task-filter-section"
        disabled={topicControlsDisabled}
      >
        <legend className="text-sm font-semibold">主题</legend>
        <div className="task-filter-options">
          <button
            type="button"
            disabled={topicControlsDisabled}
            aria-pressed={query.topic === undefined}
            onClick={() => onTopicChange(undefined)}
          >
            全部主题
          </button>
          {topics.map((topic) => (
            <button
              key={topic}
              type="button"
              disabled={topicControlsDisabled}
              aria-pressed={query.topic === topic}
              onClick={() => onTopicChange(topic)}
            >
              {topic}
            </button>
          ))}
        </div>
        {topicFeedback}
      </fieldset>

      <div className="task-filter-section">
        <label className="grid gap-2 text-sm font-medium">
          排序字段
          <select
            className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
            value={query.sort}
            disabled={disabled}
            onChange={(event) => onSortChange(event.target.value as TaskSort)}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-2 text-sm font-medium">
          排序顺序
          <select
            className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
            value={query.order}
            disabled={disabled}
            onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          >
            {ORDER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}
