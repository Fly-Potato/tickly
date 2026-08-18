import { Dialog } from "@base-ui/react/dialog"
import { useCallback, useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import {
  listParentOptions,
  type ParentTaskOption,
  type ParentOptionQuery,
} from "./task-api"

const PAGE_SIZE = 20
const SEARCH_DELAY_MS = 250

export type ParentTaskPickerProps = {
  currentTaskId: string
  value: ParentTaskOption | null
  disabled: boolean
  onChange(parent: ParentTaskOption | null): void
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError"
}

function appendUniqueOptions(
  current: ParentTaskOption[],
  incoming: ParentTaskOption[],
  currentTaskId: string
): ParentTaskOption[] {
  const knownIds = new Set(current.map((candidate) => candidate.id))
  const next = current.slice()
  for (const candidate of incoming) {
    // 前端再守一次一层关系边界，避免后端异常数据把当前任务列为自己的父级。
    if (candidate.id === currentTaskId || knownIds.has(candidate.id)) continue
    knownIds.add(candidate.id)
    next.push(candidate)
  }
  return next
}

/** 独立管理父待办候选查询；关闭、换词和卸载都会取消不再需要的请求。 */
export function ParentTaskPicker({
  currentTaskId,
  value,
  disabled,
  onChange,
}: ParentTaskPickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [items, setItems] = useState<ParentTaskOption[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [initialLoading, setInitialLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const generationRef = useRef(0)
  const lastRequestedQueryRef = useRef<string | null>("")
  const loadingMoreRef = useRef(false)
  const inflightCursorRef = useRef<string | null>(null)

  const loadFirstPage = useCallback(
    async (search: string) => {
      controllerRef.current?.abort()
      loadingMoreRef.current = false
      inflightCursorRef.current = null
      const controller = new AbortController()
      controllerRef.current = controller
      const generation = ++generationRef.current
      lastRequestedQueryRef.current = search
      setInitialLoading(true)
      setLoadingMore(false)
      setError(null)
      setItems([])
      setNextCursor(null)

      const request: ParentOptionQuery = { limit: PAGE_SIZE }
      if (search !== "") request.query = search

      try {
        const page = await listParentOptions(request, controller.signal)
        if (generationRef.current !== generation || controller.signal.aborted) {
          return
        }
        setItems(appendUniqueOptions([], page.items, currentTaskId))
        setNextCursor(page.next_cursor)
      } catch (caught) {
        if (
          generationRef.current !== generation ||
          controller.signal.aborted ||
          isAbortError(caught)
        ) {
          return
        }
        setError(safeErrorMessage(caught, "父待办加载失败"))
      } finally {
        if (
          generationRef.current === generation &&
          !controller.signal.aborted
        ) {
          setInitialLoading(false)
        }
      }
    },
    [currentTaskId]
  )

  const loadMore = useCallback(async () => {
    const cursor = nextCursor
    if (
      cursor === null ||
      initialLoading ||
      loadingMoreRef.current ||
      inflightCursorRef.current === cursor
    ) {
      return
    }
    loadingMoreRef.current = true
    inflightCursorRef.current = cursor
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const generation = ++generationRef.current
    setLoadingMore(true)
    setError(null)

    const request: ParentOptionQuery = {
      limit: PAGE_SIZE,
      cursor,
    }
    if (query !== "") request.query = query

    try {
      const page = await listParentOptions(request, controller.signal)
      if (generationRef.current !== generation || controller.signal.aborted) {
        return
      }
      setItems((current) =>
        appendUniqueOptions(current, page.items, currentTaskId)
      )
      setNextCursor(page.next_cursor)
    } catch (caught) {
      if (
        generationRef.current !== generation ||
        controller.signal.aborted ||
        isAbortError(caught)
      ) {
        return
      }
      setError(safeErrorMessage(caught, "父待办加载失败"))
    } finally {
      if (
        generationRef.current === generation &&
        inflightCursorRef.current === cursor
      ) {
        loadingMoreRef.current = false
        inflightCursorRef.current = null
        if (!controller.signal.aborted) setLoadingMore(false)
      }
    }
  }, [currentTaskId, initialLoading, nextCursor, query])

  const changeOpen = useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen)
      if (nextOpen) {
        setQuery("")
        void loadFirstPage("")
        return
      }
      generationRef.current += 1
      controllerRef.current?.abort()
      loadingMoreRef.current = false
      inflightCursorRef.current = null
    },
    [loadFirstPage]
  )

  useEffect(
    () => () => {
      generationRef.current += 1
      controllerRef.current?.abort()
      loadingMoreRef.current = false
      inflightCursorRef.current = null
    },
    []
  )

  useEffect(() => {
    if (!open || query === lastRequestedQueryRef.current) return

    // 输入变化先取消旧请求，再等待 250ms；cleanup 同时覆盖快速连续输入与关闭。
    controllerRef.current?.abort()
    const timeout = window.setTimeout(() => {
      void loadFirstPage(query)
    }, SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timeout)
      controllerRef.current?.abort()
    }
  }, [loadFirstPage, open, query])

  function changeQuery(nextQuery: string) {
    // 查询文本一旦变化，旧 cursor 和候选就不再属于当前查询，不能等防抖结束才失效。
    generationRef.current += 1
    controllerRef.current?.abort()
    loadingMoreRef.current = false
    inflightCursorRef.current = null
    lastRequestedQueryRef.current = null
    setQuery(nextQuery)
    setItems([])
    setNextCursor(null)
    setInitialLoading(true)
    setLoadingMore(false)
    setError(null)
  }

  return (
    <div className="grid gap-2 text-sm">
      <span className="font-medium">父待办</span>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground">
          {value === null ? "无父待办" : `#${value.serial} ${value.title}`}
        </span>
        <Dialog.Root open={open} onOpenChange={changeOpen}>
          <Dialog.Trigger
            render={
              <Button type="button" variant="outline" disabled={disabled} />
            }
          >
            {value === null ? "选择父待办" : "更改父待办"}
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Backdrop className="fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
            <Dialog.Viewport className="fixed inset-0 z-50 grid place-items-center p-5">
              <Dialog.Popup className="task-dialog-popup max-h-[85svh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <Dialog.Title className="text-xl font-semibold tracking-tight">
                      选择父待办
                    </Dialog.Title>
                    <Dialog.Description className="mt-2 text-sm text-muted-foreground">
                      按编号或标题查找一个根待办。
                    </Dialog.Description>
                  </div>
                  <Dialog.Close
                    render={
                      <Button
                        type="button"
                        variant="ghost"
                        aria-label="关闭父待办选择器"
                      />
                    }
                  >
                    关闭
                  </Dialog.Close>
                </div>

                <label className="mt-5 grid gap-2 text-sm font-medium">
                  搜索父待办
                  <input
                    type="search"
                    autoComplete="off"
                    className="h-11 rounded-xl border border-input bg-background px-3 transition outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"
                    value={query}
                    onChange={(event) => changeQuery(event.target.value)}
                  />
                </label>

                <div className="mt-5 grid gap-3">
                  {initialLoading ? (
                    <p role="status" className="text-sm text-muted-foreground">
                      正在加载父待办
                    </p>
                  ) : null}
                  {error !== null ? (
                    <div className="grid gap-2">
                      <p role="alert" className="text-sm text-destructive">
                        {error}
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => void loadFirstPage(query)}
                      >
                        重试父待办
                      </Button>
                    </div>
                  ) : null}
                  {!initialLoading && error === null && items.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      没有可选的父待办
                    </p>
                  ) : null}
                  {items.map((candidate) => (
                    <Button
                      key={candidate.id}
                      type="button"
                      variant="outline"
                      className="h-auto justify-start py-3 text-left whitespace-normal"
                      aria-label={`选择 #${candidate.serial} ${candidate.title} 作为父待办`}
                      onClick={() => {
                        onChange(candidate)
                        changeOpen(false)
                      }}
                    >
                      <span className="grid gap-1">
                        <span>
                          #{candidate.serial} {candidate.title}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {candidate.topic} · {candidate.status}
                        </span>
                      </span>
                    </Button>
                  ))}
                  {nextCursor !== null ? (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={loadingMore}
                      onClick={() => void loadMore()}
                    >
                      {loadingMore ? "正在加载更多父待办" : "加载更多父待办"}
                    </Button>
                  ) : null}
                </div>
              </Dialog.Popup>
            </Dialog.Viewport>
          </Dialog.Portal>
        </Dialog.Root>
        {value !== null ? (
          <Button
            type="button"
            variant="ghost"
            disabled={disabled}
            onClick={() => onChange(null)}
          >
            解除父待办
          </Button>
        ) : null}
      </div>
    </div>
  )
}
