import { useEffect, useRef, useState, type FormEvent } from "react"
import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import type { TaskCreateInput } from "./task-api"

type QuickCreateFormProps = {
  creating: boolean
  selectedTopic?: string
  topicOptions: string[]
  onCreate(input: TaskCreateInput): Promise<void>
}

export function QuickCreateForm({
  creating,
  selectedTopic,
  topicOptions,
  onCreate,
}: QuickCreateFormProps) {
  const [title, setTitle] = useState("")
  const [topic, setTopic] = useState(selectedTopic ?? "")
  const [error, setError] = useState<string | null>(null)
  const previousSelectedTopicRef = useRef(selectedTopic)

  useEffect(() => {
    const previousSelectedTopic = previousSelectedTopicRef.current
    if (selectedTopic === previousSelectedTopic) {
      return
    }

    // 筛选主题只负责预填；用户已经输入的自定义主题不能被后续筛选变化覆盖。
    setTopic((current) =>
      current === "" || current === (previousSelectedTopic ?? "")
        ? (selectedTopic ?? "")
        : current
    )
    previousSelectedTopicRef.current = selectedTopic
  }, [selectedTopic])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedTitle = title.trim()
    const normalizedTopic = topic.trim()
    if (normalizedTitle === "" || normalizedTopic === "" || creating) {
      return
    }
    setError(null)
    try {
      await onCreate({ title: normalizedTitle, topic: normalizedTopic })
      setTitle("")
      setTopic(normalizedTopic)
    } catch (caught) {
      setError(safeErrorMessage(caught, "任务创建失败"))
    }
  }

  return (
    <form className="quick-create" onSubmit={handleSubmit}>
      <label htmlFor="quick-task-title" className="sr-only">
        任务标题
      </label>
      <label htmlFor="quick-task-topic" className="sr-only">
        任务主题
      </label>
      <div className="quick-create-control">
        <input
          id="quick-task-title"
          maxLength={200}
          autoComplete="off"
          placeholder="输入任务标题，按 Enter 创建"
          value={title}
          disabled={creating}
          onChange={(event) => {
            setTitle(event.target.value)
            setError(null)
          }}
        />
        <input
          id="quick-task-topic"
          list="task-topic-options"
          maxLength={100}
          autoComplete="off"
          placeholder="输入或选择主题"
          value={topic}
          disabled={creating}
          onChange={(event) => {
            setTopic(event.target.value)
            setError(null)
          }}
        />
        <datalist id="task-topic-options">
          {topicOptions.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
        <Button
          type="submit"
          size="icon-lg"
          aria-label={creating ? "正在添加任务" : "添加任务"}
          disabled={creating || title.trim() === "" || topic.trim() === ""}
        >
          <Plus aria-hidden="true" />
        </Button>
      </div>
      {error !== null ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  )
}
