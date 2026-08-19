import { Dialog } from "@base-ui/react/dialog"
import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import type { TaskCreateInput, TaskPriority } from "./task-api"
import { TaskTimeError, toUtcDueAt } from "./task-time"

type CreateValues = {
  title: string
  description: string
  topic: string
  priority: TaskPriority | ""
  dueAt: string
}

type RequiredField = "title" | "topic"
type FieldErrors = Partial<Record<RequiredField, string>>

export type TaskCreatePanelProps = {
  selectedTopic?: string
  topicOptions: string[]
  timeZone: string
  creating: boolean
  onCreate(input: TaskCreateInput): Promise<void>
  onClose(): void
}

function validateRequiredFields(values: CreateValues): FieldErrors {
  const errors: FieldErrors = {}
  if (values.title.trim() === "") errors.title = "标题不能为空"
  if (values.topic.trim() === "") errors.topic = "主题不能为空"
  return errors
}

/**
 * 新建抽屉持有一次打开期间的表单快照。只有显式提交才越过创建边界，
 * 截止时间在该边界按账号时区转换；失败时保留输入，避免用户重复填写。
 */
export function TaskCreatePanel({
  selectedTopic,
  topicOptions,
  timeZone,
  creating,
  onCreate,
  onClose,
}: TaskCreatePanelProps) {
  const initialTopic = selectedTopic ?? ""
  const [values, setValues] = useState<CreateValues>({
    title: "",
    description: "",
    topic: initialTopic,
    priority: "",
    dueAt: "",
  })
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [timeError, setTimeError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const busy = creating || submitting
  const dirty =
    values.title.trim() !== "" ||
    values.description.trim() !== "" ||
    values.topic.trim() !== initialTopic.trim() ||
    values.priority !== "" ||
    values.dueAt !== ""

  function clearFieldError(field: RequiredField) {
    setFieldErrors((current) => {
      if (current[field] === undefined) return current
      const next = { ...current }
      delete next[field]
      return next
    })
  }

  function requestClose(cancel?: () => void) {
    if (busy) {
      cancel?.()
      return
    }
    if (dirty && !window.confirm("放弃未保存的修改？")) {
      cancel?.()
      return
    }
    onClose()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const nextFieldErrors = validateRequiredFields(values)
    setFieldErrors(nextFieldErrors)
    setTimeError(null)
    setError(null)
    if (Object.keys(nextFieldErrors).length > 0) return

    try {
      const input: TaskCreateInput = {
        title: values.title.trim(),
        topic: values.topic.trim(),
      }
      const description = values.description.trim()
      if (description !== "") input.description = description
      if (values.priority !== "") input.priority = values.priority
      if (values.dueAt !== "") {
        input.due_at = toUtcDueAt(values.dueAt, timeZone)
      }
      setSubmitting(true)
      await onCreate(input)
      onClose()
    } catch (caught) {
      if (caught instanceof TaskTimeError) {
        setTimeError(caught.message)
      } else {
        setError(safeErrorMessage(caught, "任务创建失败"))
      }
    } finally {
      setSubmitting(false)
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
          <Dialog.Popup className="task-dialog-popup task-create-popup max-h-[88svh] w-full overflow-y-auto rounded-t-3xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none md:h-full md:max-h-none md:w-[min(40rem,50vw)] md:rounded-none md:rounded-l-3xl md:p-8">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-mono text-[0.68rem] font-medium tracking-[0.18em] text-blue-600 uppercase">
                  New task
                </p>
                <Dialog.Title className="mt-2 text-2xl font-semibold tracking-tight">
                  新建待办
                </Dialog.Title>
                <Dialog.Description className="mt-2 text-sm text-muted-foreground">
                  先记录任务，再从列表中持续推进。
                </Dialog.Description>
              </div>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => requestClose()}
              >
                关闭
              </Button>
            </div>

            <form className="mt-8 space-y-6" noValidate onSubmit={handleSubmit}>
              <label className="grid gap-2 text-sm font-medium">
                标题
                <input
                  required
                  autoFocus
                  maxLength={200}
                  disabled={busy}
                  aria-invalid={fieldErrors.title !== undefined}
                  className="h-11 rounded-xl border border-input bg-background px-3 text-base transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.title}
                  onChange={(event) => {
                    clearFieldError("title")
                    setError(null)
                    setValues((current) => ({
                      ...current,
                      title: event.target.value,
                    }))
                  }}
                />
                {fieldErrors.title !== undefined ? (
                  <span role="alert" className="font-normal text-destructive">
                    {fieldErrors.title}
                  </span>
                ) : null}
              </label>

              <div className="grid gap-2 text-sm font-medium">
                <label htmlFor="task-create-description">描述（可选）</label>
                <textarea
                  id="task-create-description"
                  maxLength={4000}
                  rows={5}
                  disabled={busy}
                  aria-describedby="task-create-description-help"
                  className="resize-y rounded-xl border border-input bg-background px-3 py-2 text-sm leading-6 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.description}
                  onChange={(event) => {
                    setError(null)
                    setValues((current) => ({
                      ...current,
                      description: event.target.value,
                    }))
                  }}
                />
                <span
                  id="task-create-description-help"
                  className="text-xs font-normal text-muted-foreground"
                >
                  留空时使用标题作为描述
                </span>
              </div>

              <label className="grid gap-2 text-sm font-medium">
                主题
                <input
                  required
                  list="task-create-topic-options"
                  maxLength={100}
                  autoComplete="off"
                  disabled={busy}
                  aria-invalid={fieldErrors.topic !== undefined}
                  className="h-11 rounded-xl border border-input bg-background px-3 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.topic}
                  onChange={(event) => {
                    clearFieldError("topic")
                    setError(null)
                    setValues((current) => ({
                      ...current,
                      topic: event.target.value,
                    }))
                  }}
                />
                <datalist id="task-create-topic-options">
                  {topicOptions.map((option) => (
                    <option key={option} value={option} />
                  ))}
                </datalist>
                {fieldErrors.topic !== undefined ? (
                  <span role="alert" className="font-normal text-destructive">
                    {fieldErrors.topic}
                  </span>
                ) : null}
              </label>

              <label className="grid gap-2 text-sm font-medium">
                优先级
                <select
                  disabled={busy}
                  className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.priority}
                  onChange={(event) => {
                    setError(null)
                    setValues((current) => ({
                      ...current,
                      priority: event.target.value as TaskPriority | "",
                    }))
                  }}
                >
                  <option value="">无优先级</option>
                  <option value="low">低优先级</option>
                  <option value="medium">中优先级</option>
                  <option value="high">高优先级</option>
                </select>
              </label>

              <div className="grid gap-2 text-sm font-medium">
                <label htmlFor="task-create-due-at">截止时间</label>
                <input
                  id="task-create-due-at"
                  type="datetime-local"
                  step={60}
                  disabled={busy}
                  aria-invalid={timeError !== null}
                  className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.dueAt}
                  onChange={(event) => {
                    setTimeError(null)
                    setError(null)
                    setValues((current) => ({
                      ...current,
                      dueAt: event.target.value,
                    }))
                  }}
                />
                <span className="text-xs font-normal text-muted-foreground">
                  使用账号时区 {timeZone}
                </span>
                {timeError !== null ? (
                  <span role="alert" className="font-normal text-destructive">
                    {timeError}
                  </span>
                ) : null}
              </div>

              {error !== null ? (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              ) : null}
              {busy ? (
                <span role="status" className="sr-only" aria-live="polite">
                  正在创建待办
                </span>
              ) : null}

              <div className="flex justify-end gap-3 border-t border-border pt-5">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => requestClose()}
                >
                  取消
                </Button>
                <Button type="submit" disabled={busy}>
                  {busy ? "正在创建" : "创建待办"}
                </Button>
              </div>
            </form>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
