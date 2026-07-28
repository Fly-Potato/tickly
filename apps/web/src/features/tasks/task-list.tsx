import { Button } from "@/components/ui/button"
import type { Task, TaskStatus } from "./task-api"
import { TaskRow } from "./task-row"

type TaskListProps = {
  tasks: Task[]
  status: TaskStatus
  timeZone: string
  initialLoading: boolean
  loadingMore: boolean
  nextCursor: string | null
  error: string | null
  completionError: string | null
  completingTaskIds: ReadonlySet<string>
  onRetry(): Promise<void>
  onLoadMore(): Promise<void>
  onSelect(task: Task): void
  onCompletedChange(task: Task, completed: boolean): Promise<void>
}

const emptyMessages: Record<TaskStatus, string> = {
  all: "还没有任务，先写下第一件事。",
  active: "没有进行中的任务。",
  completed: "还没有已完成的任务。",
}

export function TaskList({
  tasks,
  status,
  timeZone,
  initialLoading,
  loadingMore,
  nextCursor,
  error,
  completionError,
  completingTaskIds,
  onRetry,
  onLoadMore,
  onSelect,
  onCompletedChange,
}: TaskListProps) {
  if (initialLoading) {
    return (
      <div role="status" aria-live="polite" className="task-loading-stack">
        <span className="sr-only">正在加载任务</span>
        <div />
        <div />
        <div />
      </div>
    )
  }

  if (error !== null && tasks.length === 0) {
    return (
      <div className="task-state-card">
        <p role="alert">{error}</p>
        <Button type="button" variant="outline" onClick={() => void onRetry()}>
          重新加载
        </Button>
      </div>
    )
  }

  return (
    <div className="task-list-panel">
      {completionError !== null ? (
        <p role="alert" className="task-inline-error">
          {completionError}
        </p>
      ) : null}
      {tasks.length === 0 ? (
        <div className="task-empty-state">
          <p>{emptyMessages[status]}</p>
          <span>快速新增会自动出现在符合当前筛选的位置。</span>
        </div>
      ) : (
        <div className="task-list">
          {tasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              timeZone={timeZone}
              completing={completingTaskIds.has(task.id)}
              onSelect={onSelect}
              onCompletedChange={onCompletedChange}
            />
          ))}
        </div>
      )}

      {error !== null && tasks.length > 0 ? (
        <div className="task-load-more-error">
          <p role="alert">{error}</p>
          <Button type="button" variant="outline" onClick={() => void onRetry()}>
            重试
          </Button>
        </div>
      ) : nextCursor !== null ? (
        <div className="task-load-more">
          <Button
            type="button"
            variant="outline"
            disabled={loadingMore}
            onClick={() => void onLoadMore()}
          >
            {loadingMore ? "正在加载" : "加载更多"}
          </Button>
        </div>
      ) : null}
      {loadingMore ? (
        <span role="status" aria-live="polite" className="sr-only">
          正在加载更多任务
        </span>
      ) : null}
    </div>
  )
}
