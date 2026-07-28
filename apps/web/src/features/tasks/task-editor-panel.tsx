import { Dialog } from "@base-ui/react/dialog"
import { useEffect, useMemo, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { DeleteTaskDialog } from "./delete-task-dialog"
import type { Task, TaskPriority, TaskUpdateInput } from "./task-api"
import {
  TaskTimeError,
  toDateTimeLocalValue,
  toUtcDueAt,
} from "./task-time"

type EditorValues = {
  title: string
  notes: string
  priority: TaskPriority
  dueAt: string
}

export type TaskEditorPanelProps = {
  task: Task
  timeZone: string
  saving: boolean
  deleting: boolean
  error: string | null
  onDirtyChange(dirty: boolean): void
  onSave(patch: TaskUpdateInput): Promise<void>
  onDelete(): Promise<void>
  onClose(): void
}

function initialValues(task: Task, timeZone: string): EditorValues {
  return {
    title: task.title,
    notes: task.notes ?? "",
    priority: task.priority,
    dueAt:
      task.due_at === null
        ? ""
        : toDateTimeLocalValue(task.due_at, timeZone),
  }
}

export function buildTaskPatch(
  task: Task,
  values: EditorValues,
  timeZone: string,
): TaskUpdateInput {
  const patch: TaskUpdateInput = {}
  const title = values.title.trim()
  const notes = values.notes === "" ? null : values.notes
  const originalDueAt =
    task.due_at === null
      ? ""
      : toDateTimeLocalValue(task.due_at, timeZone)

  if (title !== task.title) patch.title = title
  if (notes !== task.notes) patch.notes = notes
  if (values.priority !== task.priority) patch.priority = values.priority
  if (values.dueAt !== originalDueAt) {
    patch.due_at = toUtcDueAt(values.dueAt, timeZone)
  }
  return patch
}

/**
 * 编辑表单持有打开任务的独立快照，只在显式保存时生成一次最小 PATCH。
 * 账号时区转换集中在提交边界；DST 字段错误不会触发请求。关闭和切换任务前由
 * dirty 状态保护，API 失败时父工作区保留当前任务与输入。
 */
export function TaskEditorPanel({
  task,
  timeZone,
  saving,
  deleting,
  error,
  onDirtyChange,
  onSave,
  onDelete,
  onClose,
}: TaskEditorPanelProps) {
  const initial = useMemo(() => initialValues(task, timeZone), [task, timeZone])
  const [values, setValues] = useState(initial)
  const [timeError, setTimeError] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const normalizedNotes = values.notes === "" ? null : values.notes
  const dirty =
    values.title.trim() !== task.title ||
    normalizedNotes !== task.notes ||
    values.priority !== task.priority ||
    values.dueAt !== initial.dueAt

  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])

  function requestClose(cancel?: () => void) {
    if (dirty && !window.confirm("放弃未保存的修改？")) {
      cancel?.()
      return
    }
    onClose()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setTimeError(null)
    try {
      const patch = buildTaskPatch(task, values, timeZone)
      if (Object.keys(patch).length === 0) {
        return
      }
      await onSave(patch)
    } catch (caught) {
      if (caught instanceof TaskTimeError) {
        setTimeError(caught.message)
      }
    }
  }

  return (
    <Dialog.Root
      open
      onOpenChange={(open, details) => {
        if (!open) requestClose(() => details.cancel())
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="task-dialog-backdrop fixed inset-0 z-40 bg-slate-950/30 backdrop-blur-[1px]" />
        <Dialog.Viewport className="task-dialog-viewport fixed inset-0 z-40 flex items-end justify-end">
          <Dialog.Popup className="task-editor-popup max-h-[88svh] w-full overflow-y-auto rounded-t-3xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none md:h-full md:max-h-none md:w-[min(32rem,42vw)] md:rounded-none md:rounded-l-3xl md:p-8">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-mono text-[0.68rem] font-medium tracking-[0.18em] text-blue-600 uppercase">
                  Task detail
                </p>
                <Dialog.Title className="mt-2 text-2xl font-semibold tracking-tight">
                  编辑任务
                </Dialog.Title>
                <Dialog.Description className="mt-2 text-sm text-muted-foreground">
                  修改任务内容，保存后同步到你的所有设备。
                </Dialog.Description>
              </div>
              <Button
                type="button"
                variant="ghost"
                aria-label="关闭编辑面板"
                onClick={() => requestClose()}
              >
                关闭
              </Button>
            </div>

            <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
              <label className="grid gap-2 text-sm font-medium">
                标题
                <input
                  required
                  maxLength={200}
                  className="h-11 rounded-xl border border-input bg-background px-3 text-base outline-none transition focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.title}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      title: event.target.value,
                    }))
                  }
                />
              </label>

              <label className="grid gap-2 text-sm font-medium">
                备注
                <textarea
                  maxLength={4000}
                  rows={6}
                  className="resize-y rounded-xl border border-input bg-background px-3 py-2 text-sm leading-6 outline-none transition focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.notes}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      notes: event.target.value,
                    }))
                  }
                />
              </label>

              <label className="grid gap-2 text-sm font-medium">
                优先级
                <select
                  className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.priority}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      priority: event.target.value as TaskPriority,
                    }))
                  }
                >
                  <option value="none">无优先级</option>
                  <option value="low">低优先级</option>
                  <option value="medium">中优先级</option>
                  <option value="high">高优先级</option>
                </select>
              </label>

              <div className="grid gap-2 text-sm font-medium">
                <label htmlFor="task-due-at">截止时间</label>
                <input
                  id="task-due-at"
                  type="datetime-local"
                  step={60}
                  aria-invalid={timeError !== null}
                  className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.dueAt}
                  onChange={(event) => {
                    setTimeError(null)
                    setValues((current) => ({
                      ...current,
                      dueAt: event.target.value,
                    }))
                  }}
                />
                <span className="text-xs font-normal text-muted-foreground">
                  使用账号时区 {timeZone}
                </span>
              </div>

              {timeError !== null ? (
                <p role="alert" className="text-sm text-destructive">
                  {timeError}
                </p>
              ) : null}
              {error !== null ? (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              ) : null}

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
                <Button
                  type="button"
                  variant="destructive"
                  disabled={saving || deleting}
                  onClick={() => setDeleteOpen(true)}
                >
                  删除任务
                </Button>
                <div className="flex gap-3">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={saving || deleting}
                    onClick={() => requestClose()}
                  >
                    取消
                  </Button>
                  <Button
                    type="submit"
                    disabled={
                      !dirty ||
                      values.title.trim() === "" ||
                      saving ||
                      deleting
                    }
                  >
                    {saving ? "正在保存" : "保存"}
                  </Button>
                </div>
              </div>
            </form>

            <DeleteTaskDialog
              taskTitle={task.title}
              open={deleteOpen}
              deleting={deleting}
              onOpenChange={setDeleteOpen}
              onConfirm={onDelete}
            />
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
