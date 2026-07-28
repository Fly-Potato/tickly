import { useState, type FormEvent } from "react"
import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"

type QuickCreateFormProps = {
  creating: boolean
  onCreate(title: string): Promise<void>
}

export function QuickCreateForm({ creating, onCreate }: QuickCreateFormProps) {
  const [title, setTitle] = useState("")
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedTitle = title.trim()
    if (normalizedTitle === "" || creating) {
      return
    }
    setError(null)
    try {
      await onCreate(normalizedTitle)
      setTitle("")
    } catch (caught) {
      setError(safeErrorMessage(caught, "任务创建失败"))
    }
  }

  return (
    <form className="quick-create" onSubmit={handleSubmit}>
      <label htmlFor="quick-task-title" className="sr-only">
        任务标题
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
        <Button
          type="submit"
          size="icon-lg"
          aria-label={creating ? "正在添加任务" : "添加任务"}
          disabled={creating || title.trim() === ""}
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
