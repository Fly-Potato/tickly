import { Button } from "@/components/ui/button"
import {
  TaskFilterControls,
  type TaskFilterControlsProps,
} from "./task-filter-controls"

export type TaskFilterSidebarProps = TaskFilterControlsProps & {
  topicLoading: boolean
  topicError: string | null
  onRetryTopics(): Promise<void> | void
  onReset(): void
}

export function TaskFilterSidebar({
  topicLoading,
  topicError,
  onRetryTopics,
  onReset,
  ...controlProps
}: TaskFilterSidebarProps) {
  return (
    <aside className="task-filter-sidebar" aria-label="任务筛选">
      <div className="task-filter-sidebar-inner">
        <TaskFilterControls {...controlProps} topicDisabled={topicLoading} />

        {topicLoading ? (
          <p className="text-sm text-muted-foreground" role="status">
            正在加载主题…
          </p>
        ) : null}

        {topicError !== null ? (
          <div className="task-filter-error grid gap-3">
            <p className="text-sm text-destructive" role="alert">
              {topicError}
            </p>
            <Button
              type="button"
              variant="outline"
              disabled={controlProps.disabled || topicLoading}
              onClick={() => void onRetryTopics()}
            >
              重试主题
            </Button>
          </div>
        ) : null}

        <Button
          type="button"
          variant="ghost"
          disabled={controlProps.disabled}
          onClick={onReset}
        >
          清除筛选
        </Button>
      </div>
    </aside>
  )
}
