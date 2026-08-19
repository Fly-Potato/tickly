import { Button } from "@/components/ui/button"
import type { Task, TaskGroup, TaskStatus, TaskStatusFilter } from "./task-api"
import { TaskGroupView } from "./task-group"

type TaskListProps = {
  groups: TaskGroup[]
  status: TaskStatusFilter
  timeZone: string
  initialLoading: boolean
  loadingMore: boolean
  nextCursor: string | null
  error: string | null
  statusError: string | null
  statusMutatingTaskIds: ReadonlySet<string>
  onRetry(): Promise<void>
  onLoadMore(): Promise<void>
  onSelect(task: Task): void
  onStatusChange(task: Task, status: TaskStatus): Promise<void>
}

const emptyMessages: Record<TaskStatusFilter, string> = {
  all: "还没有任务，先写下第一件事。",
  new: "还没有新任务。",
  in_progress: "没有进行中的任务。",
  completed: "还没有已完成的任务。",
}

export function TaskList({
  groups,
  status,
  timeZone,
  initialLoading,
  loadingMore,
  nextCursor,
  error,
  statusError,
  statusMutatingTaskIds,
  onRetry,
  onLoadMore,
  onSelect,
  onStatusChange,
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

  if (error !== null && groups.length === 0) {
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
      {statusError !== null ? (
        <p role="alert" className="task-inline-error">
          {statusError}
        </p>
      ) : null}
      {groups.length === 0 ? (
        <div className="task-empty-state">
          <p>{emptyMessages[status]}</p>
          <span>新建的待办会自动出现在符合当前筛选的位置。</span>
        </div>
      ) : (
        <table className="task-table">
          <caption className="sr-only">Todo List</caption>
          <colgroup>
            <col className="task-table-col-serial" />
            <col className="task-table-col-task" />
            <col className="task-table-col-topic" />
            <col className="task-table-col-priority" />
            <col className="task-table-col-due" />
            <col className="task-table-col-status" />
          </colgroup>
          <thead className="task-table-head">
            <tr>
              <th scope="col">#</th>
              <th scope="col">待办</th>
              <th scope="col">主题</th>
              <th scope="col">优先级</th>
              <th scope="col">截止时间</th>
              <th scope="col">状态</th>
            </tr>
          </thead>
          {groups.map((group) => (
            <TaskGroupView
              key={group.task.id}
              group={group}
              timeZone={timeZone}
              statusMutatingTaskIds={statusMutatingTaskIds}
              onSelect={onSelect}
              onStatusChange={onStatusChange}
            />
          ))}
        </table>
      )}

      {error !== null && groups.length > 0 ? (
        <div className="task-load-more-error">
          <p role="alert">{error}</p>
          <Button
            type="button"
            variant="outline"
            onClick={() => void onRetry()}
          >
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
