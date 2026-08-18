import { Dialog } from "@base-ui/react/dialog"
import { useEffect, useMemo, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { ChildTaskCreateForm } from "./child-task-create-form"
import { DeleteTaskDialog } from "./delete-task-dialog"
import { ParentTaskPicker } from "./parent-task-picker"
import type {
  ParentTaskOption,
  Task,
  TaskCreateInput,
  TaskPriority,
  TaskStatus,
  TaskUpdateInput,
} from "./task-api"
import {
  formatTaskTimestamp,
  TaskTimeError,
  toDateTimeLocalValue,
  toUtcDueAt,
} from "./task-time"

type EditorValues = {
  title: string
  description: string
  topic: string
  priority: TaskPriority | ""
  status: TaskStatus
  dueAt: string
  parentId: string
}

type RequiredField = "title" | "description" | "topic"
type FieldErrors = Partial<Record<RequiredField, string>>

export type TaskEditorPanelProps = {
  task: Task
  childCount: number
  currentParent: ParentTaskOption | null
  timeZone: string
  saving: boolean
  deleting: boolean
  creatingChild: boolean
  error: string | null
  onDirtyChange(dirty: boolean): void
  onSave(patch: TaskUpdateInput): Promise<void>
  onDelete(): Promise<void>
  onCreateChild(input: TaskCreateInput): Promise<void>
  onClose(): void
}

function initialValues(task: Task, timeZone: string): EditorValues {
  return {
    title: task.title,
    description: task.description,
    topic: task.topic,
    priority: task.priority ?? "",
    status: task.status,
    dueAt:
      task.due_at === null ? "" : toDateTimeLocalValue(task.due_at, timeZone),
    parentId: task.parent_id ?? "",
  }
}

function normalizeValues(values: EditorValues) {
  return {
    title: values.title.trim(),
    description: values.description.trim(),
    topic: values.topic.trim(),
    priority: values.priority === "" ? null : values.priority,
    status: values.status,
    dueAt: values.dueAt,
    parentId: values.parentId === "" ? null : values.parentId,
  }
}

function validateRequiredFields(values: EditorValues): FieldErrors {
  const normalized = normalizeValues(values)
  const errors: FieldErrors = {}
  if (normalized.title === "") errors.title = "标题不能为空"
  if (normalized.description === "") errors.description = "描述不能为空"
  if (normalized.topic === "") errors.topic = "主题不能为空"
  return errors
}

function buildTaskPatch(
  task: Task,
  values: EditorValues,
  timeZone: string
): TaskUpdateInput {
  const patch: TaskUpdateInput = {}
  const normalized = normalizeValues(values)
  const originalDueAt =
    task.due_at === null ? "" : toDateTimeLocalValue(task.due_at, timeZone)

  // API 只接收规范化后的真实变化，避免空字符串与 null 在往返时产生伪修改。
  if (normalized.title !== task.title) patch.title = normalized.title
  if (normalized.description !== task.description) {
    patch.description = normalized.description
  }
  if (normalized.topic !== task.topic) patch.topic = normalized.topic
  if (normalized.priority !== task.priority) {
    patch.priority = normalized.priority
  }
  if (normalized.status !== task.status) patch.status = normalized.status
  if (normalized.dueAt !== originalDueAt) {
    patch.due_at = toUtcDueAt(normalized.dueAt, timeZone)
  }
  if (normalized.parentId !== task.parent_id) {
    patch.parent_id = normalized.parentId
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
  childCount,
  currentParent,
  timeZone,
  saving,
  deleting,
  creatingChild,
  error,
  onDirtyChange,
  onSave,
  onDelete,
  onCreateChild,
  onClose,
}: TaskEditorPanelProps) {
  const initial = useMemo(() => initialValues(task, timeZone), [task, timeZone])
  const [values, setValues] = useState(initial)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [timeError, setTimeError] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [parentDraft, setParentDraft] = useState(currentParent)
  const normalized = normalizeValues(values)
  const dirty =
    normalized.title !== task.title ||
    normalized.description !== task.description ||
    normalized.topic !== task.topic ||
    normalized.priority !== task.priority ||
    normalized.status !== task.status ||
    normalized.dueAt !== initial.dueAt ||
    normalized.parentId !== task.parent_id

  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])

  function clearFieldError(field: RequiredField) {
    setFieldErrors((current) => {
      if (current[field] === undefined) return current
      const next = { ...current }
      delete next[field]
      return next
    })
  }

  function requestClose(cancel?: () => void) {
    if (dirty && !window.confirm("放弃未保存的修改？")) {
      cancel?.()
      return
    }
    onClose()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const nextFieldErrors = validateRequiredFields(values)
    setFieldErrors(nextFieldErrors)
    setTimeError(null)
    if (Object.keys(nextFieldErrors).length > 0) {
      return
    }
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
          <Dialog.Popup className="task-dialog-popup task-editor-popup max-h-[88svh] w-full overflow-y-auto rounded-t-3xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none md:h-full md:max-h-none md:w-[min(32rem,42vw)] md:rounded-none md:rounded-l-3xl md:p-8">
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

            <form className="mt-8 space-y-6" noValidate onSubmit={handleSubmit}>
              <div className="grid gap-1 text-sm">
                <span className="font-medium text-muted-foreground">编号</span>
                <span className="font-mono text-base">#{task.serial}</span>
              </div>

              <label className="grid gap-2 text-sm font-medium">
                标题
                <input
                  required
                  maxLength={200}
                  aria-invalid={fieldErrors.title !== undefined}
                  className="h-11 rounded-xl border border-input bg-background px-3 text-base transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.title}
                  onChange={(event) => {
                    clearFieldError("title")
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

              <label className="grid gap-2 text-sm font-medium">
                描述
                <textarea
                  required
                  maxLength={4000}
                  rows={6}
                  aria-invalid={fieldErrors.description !== undefined}
                  className="resize-y rounded-xl border border-input bg-background px-3 py-2 text-sm leading-6 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.description}
                  onChange={(event) => {
                    clearFieldError("description")
                    setValues((current) => ({
                      ...current,
                      description: event.target.value,
                    }))
                  }}
                />
                {fieldErrors.description !== undefined ? (
                  <span role="alert" className="font-normal text-destructive">
                    {fieldErrors.description}
                  </span>
                ) : null}
              </label>

              <label className="grid gap-2 text-sm font-medium">
                主题
                <input
                  required
                  maxLength={100}
                  aria-invalid={fieldErrors.topic !== undefined}
                  className="h-11 rounded-xl border border-input bg-background px-3 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.topic}
                  onChange={(event) => {
                    clearFieldError("topic")
                    setValues((current) => ({
                      ...current,
                      topic: event.target.value,
                    }))
                  }}
                />
                {fieldErrors.topic !== undefined ? (
                  <span role="alert" className="font-normal text-destructive">
                    {fieldErrors.topic}
                  </span>
                ) : null}
              </label>

              <label className="grid gap-2 text-sm font-medium">
                状态
                <select
                  className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.status}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      status: event.target.value as TaskStatus,
                    }))
                  }
                >
                  <option value="new">新建</option>
                  <option value="in_progress">进行中</option>
                  <option value="completed">已完成</option>
                </select>
              </label>

              <label className="grid gap-2 text-sm font-medium">
                优先级
                <select
                  className="h-11 rounded-xl border border-input bg-background px-3 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                  value={values.priority}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      priority: event.target.value as TaskPriority | "",
                    }))
                  }
                >
                  <option value="">无优先级</option>
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
                {timeError !== null ? (
                  <span role="alert" className="font-normal text-destructive">
                    {timeError}
                  </span>
                ) : null}
              </div>

              <ParentTaskPicker
                currentTaskId={task.id}
                value={parentDraft}
                disabled={task.parent_id === null && childCount > 0}
                onChange={(parent) => {
                  // 父级选择只更新编辑快照，仍由“保存”统一越过 PATCH 边界。
                  setParentDraft(parent)
                  setValues((current) => ({
                    ...current,
                    parentId: parent?.id ?? "",
                  }))
                }}
              />
              {task.parent_id === null && childCount > 0 ? (
                <p className="text-sm text-muted-foreground">
                  一层父子关系下，拥有子待办的任务不能再成为子待办
                </p>
              ) : null}

              <div className="grid gap-1 text-sm">
                <span className="font-medium text-muted-foreground">
                  创建时间
                </span>
                <span>
                  {formatTaskTimestamp(task.created_at, timeZone, "创建")}
                </span>
              </div>

              {task.status === "completed" && task.completed_at !== null ? (
                <div className="grid gap-1 text-sm">
                  <span className="font-medium text-muted-foreground">
                    完成时间
                  </span>
                  <span>
                    {formatTaskTimestamp(task.completed_at, timeZone, "完成")}
                  </span>
                </div>
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
                  disabled={saving || deleting || creatingChild}
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
                    disabled={!dirty || saving || deleting || creatingChild}
                  >
                    {saving ? "正在保存" : "保存"}
                  </Button>
                </div>
              </div>
            </form>

            {task.parent_id === null ? (
              <ChildTaskCreateForm
                parent={task}
                creating={creatingChild}
                disabled={saving || deleting}
                onCreate={onCreateChild}
              />
            ) : null}

            <DeleteTaskDialog
              taskTitle={task.title}
              childCount={childCount}
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
