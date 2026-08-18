import { Dialog } from "@base-ui/react/dialog"
import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { DEFAULT_TASK_QUERY } from "./task-api"
import { TaskFilterControls } from "./task-filter-controls"
import type { WorkspaceQuery } from "./use-task-workspace"

type MobileTaskFilterDialogProps = {
  query: WorkspaceQuery
  topics: string[]
  disabled: boolean
  topicLoading: boolean
  topicError: string | null
  onRetryTopics(): Promise<void> | void
  onApply(query: WorkspaceQuery): void
}

type MobileFilterFormProps = MobileTaskFilterDialogProps & {
  onApply(query: WorkspaceQuery): void
}

/**
 * 移动筛选表单持有独立草稿，只有显式提交才越过组件边界更新列表查询。
 * 取消、关闭或 Escape 会直接卸载该快照，因此不会泄漏未应用的选择。
 */
function MobileFilterForm({
  query,
  topics,
  disabled,
  topicLoading,
  topicError,
  onRetryTopics,
  onApply,
}: MobileFilterFormProps) {
  const [draft, setDraft] = useState<WorkspaceQuery>(query)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onApply(draft)
  }

  return (
    <form
      className="mobile-task-filter-form grid gap-6"
      onSubmit={handleSubmit}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <Dialog.Title className="text-xl font-semibold tracking-tight">
            筛选与排序
          </Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">
            调整筛选后点击应用，任务列表才会更新。
          </Dialog.Description>
        </div>
        <Dialog.Close
          render={
            <Button type="button" variant="ghost" aria-label="关闭筛选" />
          }
        >
          关闭
        </Dialog.Close>
      </div>

      <TaskFilterControls
        query={draft}
        topics={topics}
        disabled={disabled}
        topicDisabled={topicLoading}
        topicFeedback={
          <>
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
                  disabled={disabled || topicLoading}
                  onClick={() => void onRetryTopics()}
                >
                  重试主题
                </Button>
              </div>
            ) : null}
          </>
        }
        onStatusChange={(status) =>
          setDraft((current) => ({ ...current, status }))
        }
        onTopicChange={(topic) =>
          setDraft((current) => {
            const next = { ...current }
            if (topic === undefined) {
              delete next.topic
            } else {
              next.topic = topic
            }
            return next
          })
        }
        onSortChange={(sort) => setDraft((current) => ({ ...current, sort }))}
        onOrderChange={(order) =>
          setDraft((current) => ({ ...current, order }))
        }
      />

      <div className="flex flex-wrap justify-end gap-3">
        <Button
          type="button"
          variant="ghost"
          disabled={disabled}
          onClick={() => setDraft({ ...DEFAULT_TASK_QUERY })}
        >
          清除筛选
        </Button>
        <Dialog.Close render={<Button type="button" variant="outline" />}>
          取消
        </Dialog.Close>
        <Button type="submit" disabled={disabled}>
          应用筛选
        </Button>
      </div>
    </form>
  )
}

export function MobileTaskFilterDialog({
  query,
  topics,
  disabled,
  topicLoading,
  topicError,
  onRetryTopics,
  onApply,
}: MobileTaskFilterDialogProps) {
  const [open, setOpen] = useState(false)
  const formKey = `${query.status}:${query.topic ?? "all"}:${query.sort}:${query.order}`

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger
        render={
          <Button
            type="button"
            variant="outline"
            className="mobile-task-filter-trigger"
            disabled={disabled}
          />
        }
      >
        筛选
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-slate-950/40 backdrop-blur-[2px]" />
        <Dialog.Viewport className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center">
          <Dialog.Popup className="task-dialog-popup max-h-[90svh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none">
            {open ? (
              <MobileFilterForm
                key={formKey}
                query={query}
                topics={topics}
                disabled={disabled}
                topicLoading={topicLoading}
                topicError={topicError}
                onRetryTopics={onRetryTopics}
                onApply={(draft) => {
                  onApply(draft)
                  setOpen(false)
                }}
              />
            ) : null}
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
