import { useEffect, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import type { Task, TaskCreateInput } from "./task-api"

export type ChildTaskCreateFormProps = {
  parent: Task
  creating: boolean
  disabled: boolean
  onCreate(input: TaskCreateInput): Promise<void>
}

/** 子待办创建失败时保留草稿；只有服务端成功后才清空标题。 */
export function ChildTaskCreateForm({
  parent,
  creating,
  disabled,
  onCreate,
}: ChildTaskCreateFormProps) {
  const [title, setTitle] = useState("")
  const [topic, setTopic] = useState(parent.topic)
  const [error, setError] = useState<string | null>(null)
  const submittingRef = useRef(false)
  const mountedRef = useRef(false)
  const generationRef = useRef(0)
  const previousParentTopicRef = useRef(parent.topic)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      generationRef.current += 1
      submittingRef.current = false
    }
  }, [])

  useEffect(() => {
    const previousParentTopic = previousParentTopicRef.current
    previousParentTopicRef.current = parent.topic
    // 只让未形成独立草稿的主题跟随父任务，避免服务端刷新覆盖用户输入。
    setTopic((current) =>
      current === "" || current === previousParentTopic ? parent.topic : current
    )
  }, [parent.topic])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedTitle = title.trim()
    const normalizedTopic = topic.trim()
    if (
      normalizedTitle === "" ||
      normalizedTopic === "" ||
      creating ||
      disabled ||
      submittingRef.current
    ) {
      return
    }

    // ref 在 await 前同步占权，避免父级 creating 尚未 rerender 时重复越过创建边界。
    submittingRef.current = true
    const generation = ++generationRef.current
    setError(null)
    try {
      await onCreate({
        title: normalizedTitle,
        topic: normalizedTopic,
        parent_id: parent.id,
      })
      if (!mountedRef.current || generationRef.current !== generation) return
      setTitle("")
      setTopic(normalizedTopic)
    } catch (caught) {
      if (!mountedRef.current || generationRef.current !== generation) return
      setError(safeErrorMessage(caught, "子待办创建失败"))
    } finally {
      if (generationRef.current === generation) submittingRef.current = false
    }
  }

  return (
    <form
      className="mt-6 grid gap-4 border-t border-border pt-5"
      onSubmit={handleSubmit}
    >
      <h3 className="font-semibold">添加子待办</h3>
      <label className="grid gap-2 text-sm font-medium">
        子待办标题
        <input
          required
          maxLength={200}
          autoComplete="off"
          className="h-11 rounded-xl border border-input bg-background px-3 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
          value={title}
          disabled={creating || disabled}
          onChange={(event) => {
            setTitle(event.target.value)
            setError(null)
          }}
        />
      </label>
      <label className="grid gap-2 text-sm font-medium">
        子待办主题
        <input
          required
          maxLength={100}
          autoComplete="off"
          className="h-11 rounded-xl border border-input bg-background px-3 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
          value={topic}
          disabled={creating || disabled}
          onChange={(event) => {
            setTopic(event.target.value)
            setError(null)
          }}
        />
      </label>
      {error !== null ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button
        type="submit"
        disabled={
          creating || disabled || title.trim() === "" || topic.trim() === ""
        }
      >
        {creating ? "正在添加子待办" : "添加子待办"}
      </Button>
    </form>
  )
}
