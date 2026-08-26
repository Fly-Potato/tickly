import { Dialog } from "@base-ui/react/dialog"
import {
  Copy,
  KeyRound,
  Plus,
  RotateCcw,
  ShieldCheck,
  Trash2,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import {
  createMcpToken,
  listMcpTokens,
  revokeMcpToken,
  type McpToken,
  type McpTokenExpiryDays,
  type McpTokenStatus,
} from "./settings-api"

const STATUS_LABELS: Record<McpTokenStatus, string> = {
  active: "有效",
  expired: "已过期",
  revoked: "已撤销",
}

type LoadMode = "initial" | "retry" | null

type LocalMutation =
  | {
      kind: "created"
      version: number
      item: McpToken
    }
  | {
      kind: "revoked"
      version: number
      item: McpToken
    }

function expiryValue(value: string): McpTokenExpiryDays {
  if (value === "90") {
    return 90
  }
  if (value === "never") {
    return null
  }
  return 365
}

function dateFormatter(timeZone: string) {
  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }
  try {
    return new Intl.DateTimeFormat("zh-CN", options)
  } catch {
    // 账号时区由服务端校验；历史脏数据仍回退 UTC，避免整张设置页崩溃。
    return new Intl.DateTimeFormat("zh-CN", { ...options, timeZone: "UTC" })
  }
}

function displayDate(formatter: Intl.DateTimeFormat, value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return "时间未知"
  }
  try {
    return formatter.format(date)
  } catch {
    return "时间未知"
  }
}

function shortTokenId(id: string) {
  return id.slice(0, 8)
}

function deduplicateTokens(items: McpToken[]) {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.id)) {
      return false
    }
    seen.add(item.id)
    return true
  })
}

function mergeTokenSnapshot(
  snapshot: McpToken[],
  current: McpToken[],
  localMutations: Map<string, LocalMutation>,
  startMutationVersion: number
) {
  const deduplicatedSnapshot = deduplicateTokens(snapshot)
  const newerMutations = new Map(
    Array.from(localMutations.entries()).filter(
      ([, mutation]) => mutation.version > startMutationVersion
    )
  )
  if (newerMutations.size === 0) {
    return deduplicatedSnapshot
  }

  const snapshotIds = new Set(deduplicatedSnapshot.map((item) => item.id))
  // 快照缺失的本地变更按当前视觉顺序置前；已进入快照的创建项完全信任服务端新元数据。
  const localOnly = deduplicateTokens(current)
    .filter((item) => newerMutations.has(item.id) && !snapshotIds.has(item.id))
    .map((item) => newerMutations.get(item.id)?.item ?? item)
  const mergedSnapshot = deduplicatedSnapshot.map((item) => {
    const mutation = newerMutations.get(item.id)
    if (mutation?.kind === "revoked") {
      // 撤销是请求发出后的权限收紧，只覆盖状态；时间等字段仍采用更新的服务端快照。
      return { ...item, status: "revoked" as const }
    }
    return item
  })
  return [...localOnly, ...mergedSnapshot]
}

export function McpTokenSettings({ timeZone }: { timeZone: string }) {
  const [tokens, setTokens] = useState<McpToken[]>([])
  const [loadMode, setLoadMode] = useState<LoadMode>("initial")
  const [listError, setListError] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [expiry, setExpiry] = useState("365")
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [issuedToken, setIssuedToken] = useState<string | null>(null)
  const [copying, setCopying] = useState(false)
  const [copyMessage, setCopyMessage] = useState<string | null>(null)
  const [copyError, setCopyError] = useState<string | null>(null)
  const [selected, setSelected] = useState<McpToken | null>(null)
  const [revoking, setRevoking] = useState(false)
  const [revokeError, setRevokeError] = useState<string | null>(null)
  const activeRef = useRef(false)
  const loadGenerationRef = useRef(0)
  const loadRunningRef = useRef(false)
  const mutationVersionRef = useRef(0)
  const localMutationsRef = useRef(new Map<string, LocalMutation>())
  const copyGenerationRef = useRef(0)
  const issuedTokenRef = useRef<string | null>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const revokeTriggerRef = useRef<HTMLButtonElement | null>(null)
  const tokenRowRefs = useRef(new Map<string, HTMLLIElement>())
  const successfulRevokeIdRef = useRef<string | null>(null)
  const formatter = useMemo(() => dateFormatter(timeZone), [timeZone])
  const endpoint = `${window.location.origin}/mcp`

  async function retryTokens() {
    if (loadRunningRef.current) {
      return
    }
    loadRunningRef.current = true
    const generation = ++loadGenerationRef.current
    const startMutationVersion = mutationVersionRef.current
    setLoadMode("retry")
    setListError(null)
    try {
      const items = await listMcpTokens()
      if (activeRef.current && loadGenerationRef.current === generation) {
        setTokens((current) =>
          mergeTokenSnapshot(
            items,
            current,
            localMutationsRef.current,
            startMutationVersion
          )
        )
      }
    } catch (caught) {
      if (activeRef.current && loadGenerationRef.current === generation) {
        setListError(safeErrorMessage(caught, "MCP Token 加载失败"))
      }
    } finally {
      if (loadGenerationRef.current === generation) {
        loadRunningRef.current = false
        if (activeRef.current) {
          setLoadMode(null)
        }
      }
    }
  }

  useEffect(() => {
    activeRef.current = true
    loadRunningRef.current = true
    const generation = ++loadGenerationRef.current
    const startMutationVersion = mutationVersionRef.current
    void listMcpTokens()
      .then((items) => {
        if (activeRef.current && loadGenerationRef.current === generation) {
          setTokens((current) =>
            mergeTokenSnapshot(
              items,
              current,
              localMutationsRef.current,
              startMutationVersion
            )
          )
        }
      })
      .catch((caught: unknown) => {
        if (activeRef.current && loadGenerationRef.current === generation) {
          setListError(safeErrorMessage(caught, "MCP Token 加载失败"))
        }
      })
      .finally(() => {
        if (loadGenerationRef.current === generation) {
          loadRunningRef.current = false
          if (activeRef.current) {
            setLoadMode(null)
          }
        }
      })
    return () => {
      activeRef.current = false
      loadGenerationRef.current += 1
      loadRunningRef.current = false
      copyGenerationRef.current += 1
      issuedTokenRef.current = null
    }
  }, [])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (creating) {
      return
    }
    const trimmedName = name.trim()
    if (trimmedName.length === 0) {
      return
    }

    setCreating(true)
    setCreateError(null)
    try {
      const issued = await createMcpToken(trimmedName, expiryValue(expiry))
      const mutationVersion = ++mutationVersionRef.current
      localMutationsRef.current.set(issued.item.id, {
        kind: "created",
        version: mutationVersion,
        item: issued.item,
      })
      setTokens((current) => [
        issued.item,
        ...current.filter((item) => item.id !== issued.item.id),
      ])
      setName("")
      copyGenerationRef.current += 1
      issuedTokenRef.current = issued.token
      setIssuedToken(issued.token)
      setCopying(false)
      setCopyMessage(null)
      setCopyError(null)
    } catch (caught) {
      setCreateError(safeErrorMessage(caught, "MCP Token 创建失败"))
    } finally {
      setCreating(false)
    }
  }

  function closeIssuedToken() {
    // 原始 Token 只活在这一段内存状态；任何关闭路径都不可恢复地清空它。
    copyGenerationRef.current += 1
    issuedTokenRef.current = null
    setIssuedToken(null)
    setCopyMessage(null)
    setCopyError(null)
    setCopying(false)
  }

  async function copyIssuedToken() {
    if (issuedToken === null || copying) {
      return
    }
    const token = issuedToken
    const generation = ++copyGenerationRef.current
    setCopying(true)
    setCopyMessage(null)
    setCopyError(null)
    try {
      await navigator.clipboard.writeText(token)
      if (
        activeRef.current &&
        copyGenerationRef.current === generation &&
        issuedTokenRef.current === token
      ) {
        setCopyMessage("Token 已复制")
      }
    } catch {
      if (
        activeRef.current &&
        copyGenerationRef.current === generation &&
        issuedTokenRef.current === token
      ) {
        setCopyError("复制失败，请重试")
      }
    } finally {
      if (
        activeRef.current &&
        copyGenerationRef.current === generation &&
        issuedTokenRef.current === token
      ) {
        setCopying(false)
      }
    }
  }

  function openRevoke(token: McpToken, trigger: HTMLButtonElement) {
    successfulRevokeIdRef.current = null
    revokeTriggerRef.current = trigger
    setSelected(token)
    setRevokeError(null)
  }

  function changeRevokeOpen(open: boolean) {
    if (!open && !revoking) {
      setSelected(null)
      setRevokeError(null)
    }
  }

  async function confirmRevoke() {
    if (selected === null || revoking) {
      return
    }
    const tokenId = selected.id
    setRevoking(true)
    setRevokeError(null)
    try {
      await revokeMcpToken(tokenId)
      // 来源按钮会随 revoked 状态被移除；先记录仍稳定存在的行，让 Dialog 关闭时回焦到同一 Token 上下文。
      successfulRevokeIdRef.current = tokenId
      const revokedToken = { ...selected, status: "revoked" as const }
      const mutationVersion = ++mutationVersionRef.current
      localMutationsRef.current.set(tokenId, {
        kind: "revoked",
        version: mutationVersion,
        item: revokedToken,
      })
      setTokens((current) =>
        current.map((token) => (token.id === tokenId ? revokedToken : token))
      )
      setSelected(null)
    } catch (caught) {
      setRevokeError(safeErrorMessage(caught, "MCP Token 撤销失败"))
    } finally {
      setRevoking(false)
    }
  }

  return (
    <section
      className="settings-tool-card"
      aria-labelledby="mcp-token-settings-title"
    >
      <div className="settings-card-heading">
        <span className="settings-card-icon" aria-hidden="true">
          <ShieldCheck />
        </span>
        <div>
          <h2 id="mcp-token-settings-title">MCP Token</h2>
          <p>为每台设备创建独立凭据，并在不再使用时单独撤销。</p>
        </div>
      </div>

      <form className="settings-token-create" onSubmit={handleCreate}>
        <div className="settings-field settings-token-name-field">
          <label htmlFor="settings-token-name">Token 名称</label>
          <input
            ref={nameInputRef}
            id="settings-token-name"
            value={name}
            onChange={(event) => {
              setName(event.target.value)
              setCreateError(null)
            }}
            maxLength={64}
            required
            disabled={creating}
          />
        </div>
        <div className="settings-field settings-token-expiry-field">
          <label htmlFor="settings-token-expiry">有效期</label>
          <select
            id="settings-token-expiry"
            value={expiry}
            onChange={(event) => setExpiry(event.target.value)}
            disabled={creating}
          >
            <option value="90">90 天</option>
            <option value="365">365 天（默认）</option>
            <option value="never">永不过期</option>
          </select>
        </div>
        <Button
          type="submit"
          className="settings-create-token-button"
          disabled={creating}
        >
          <Plus aria-hidden="true" />
          {creating ? "正在创建" : "创建 Token"}
        </Button>
      </form>

      {createError ? (
        <p role="alert" className="settings-inline-error">
          {createError}
        </p>
      ) : null}

      <div className="settings-token-list" aria-live="polite">
        {loadMode === "initial" ? (
          <p role="status" className="settings-list-state">
            正在加载 Token…
          </p>
        ) : null}
        {listError || loadMode === "retry" ? (
          <div className="settings-list-error">
            {listError ? (
              <p role="alert">{listError}</p>
            ) : (
              <p role="status">正在重新加载 Token…</p>
            )}
            <Button
              type="button"
              variant="outline"
              disabled={loadMode === "retry"}
              onClick={() => void retryTokens()}
            >
              <RotateCcw aria-hidden="true" />
              {loadMode === "retry" ? "正在重试" : "重试"}
            </Button>
          </div>
        ) : null}
        {tokens.length === 0 && loadMode === null && listError === null ? (
          <div className="settings-token-empty">
            <KeyRound aria-hidden="true" />
            <p>还没有 MCP Token</p>
            <span>填写上方名称，为当前设备创建一个。</span>
          </div>
        ) : null}
        {tokens.length > 0 ? (
          <ul className="settings-token-rows">
            {tokens.map((token) => {
              const shortId = shortTokenId(token.id)
              return (
                <li
                  key={token.id}
                  className="settings-token-row"
                  data-testid="mcp-token-row"
                  aria-label={`MCP Token：${token.name}，ID ${shortId}`}
                  tabIndex={-1}
                  ref={(element) => {
                    if (element === null) {
                      tokenRowRefs.current.delete(token.id)
                    } else {
                      tokenRowRefs.current.set(token.id, element)
                    }
                  }}
                >
                  <div className="settings-token-identity">
                    <strong>{token.name}</strong>
                    <span className="settings-token-id">ID {shortId}</span>
                    <span className="settings-token-created">
                      创建于 {displayDate(formatter, token.created_at)}
                    </span>
                  </div>
                  <span
                    className="settings-token-status"
                    data-status={token.status}
                  >
                    {STATUS_LABELS[token.status]}
                  </span>
                  <dl className="settings-token-dates">
                    <div>
                      <dt>到期</dt>
                      <dd>
                        {token.expires_at === null
                          ? "永不过期"
                          : displayDate(formatter, token.expires_at)}
                      </dd>
                    </div>
                    <div>
                      <dt>最近使用</dt>
                      <dd>
                        {token.last_used_at === null
                          ? "从未使用"
                          : displayDate(formatter, token.last_used_at)}
                      </dd>
                    </div>
                  </dl>
                  {token.status !== "revoked" ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="settings-revoke-button"
                      aria-label={`撤销 ${token.name}，ID ${shortId}`}
                      onClick={(event) =>
                        openRevoke(token, event.currentTarget)
                      }
                    >
                      <Trash2 aria-hidden="true" />
                      撤销
                    </Button>
                  ) : null}
                </li>
              )
            })}
          </ul>
        ) : null}
      </div>

      <Dialog.Root
        open={issuedToken !== null}
        onOpenChange={(open) => {
          if (!open) {
            closeIssuedToken()
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="settings-dialog-backdrop fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
          <Dialog.Viewport className="fixed inset-0 z-50 grid place-items-center overflow-y-auto p-5">
            <Dialog.Popup
              finalFocus={nameInputRef}
              className="settings-dialog-popup w-full max-w-xl rounded-2xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none"
            >
              <Dialog.Title className="text-xl font-semibold tracking-tight">
                保存 MCP Token
              </Dialog.Title>
              <Dialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                这是唯一一次显示原始
                Token。关闭后无法再次查看，请先保存到设备的安全环境变量中。
              </Dialog.Description>

              {issuedToken !== null ? (
                <code className="settings-issued-token">{issuedToken}</code>
              ) : null}

              <div className="settings-token-guide">
                <p>
                  服务地址：<code>{endpoint}</code>
                </p>
                <p>
                  环境变量：<code>TICKLY_MCP_TOKEN</code>
                </p>
                <p>
                  请求认证：<code>Authorization: Bearer &lt;Token&gt;</code>
                </p>
                <p>
                  Codex 可使用{" "}
                  <code>--bearer-token-env-var TICKLY_MCP_TOKEN</code>
                  读取该变量。
                </p>
              </div>

              {copyMessage ? (
                <p role="status" className="settings-copy-status">
                  {copyMessage}
                </p>
              ) : null}
              {copyError ? (
                <p role="alert" className="settings-inline-error">
                  {copyError}
                </p>
              ) : null}

              <div className="settings-dialog-actions">
                <Button
                  type="button"
                  variant="outline"
                  disabled={copying}
                  onClick={() => void copyIssuedToken()}
                >
                  <Copy aria-hidden="true" />
                  {copying ? "正在复制" : "复制 Token"}
                </Button>
                <Button type="button" onClick={closeIssuedToken}>
                  我已保存，关闭
                </Button>
              </div>
            </Dialog.Popup>
          </Dialog.Viewport>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={selected !== null} onOpenChange={changeRevokeOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="settings-dialog-backdrop fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
          <Dialog.Viewport className="fixed inset-0 z-50 grid place-items-center p-5">
            <Dialog.Popup
              finalFocus={() => {
                const revokedId = successfulRevokeIdRef.current
                if (revokedId !== null) {
                  return (
                    tokenRowRefs.current.get(revokedId) ?? nameInputRef.current
                  )
                }
                return revokeTriggerRef.current
              }}
              className="settings-dialog-popup w-full max-w-md rounded-2xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none"
            >
              <Dialog.Title className="text-xl font-semibold tracking-tight">
                撤销 MCP Token
              </Dialog.Title>
              <Dialog.Description className="settings-revoke-description mt-3 text-sm leading-6 text-muted-foreground">
                撤销“{selected?.name}
                ”（ID {selected === null ? "" : shortTokenId(selected.id)}）后，
                使用它的客户端会立即失去访问权限。此操作无法撤销。
              </Dialog.Description>
              {revokeError ? (
                <p role="alert" className="settings-inline-error mt-4">
                  {revokeError}
                </p>
              ) : null}
              <div className="settings-dialog-actions">
                <Button
                  type="button"
                  variant="outline"
                  disabled={revoking}
                  onClick={() => changeRevokeOpen(false)}
                >
                  取消
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  disabled={revoking}
                  onClick={() => void confirmRevoke()}
                >
                  <Trash2 aria-hidden="true" />
                  {revoking ? "正在撤销" : "确认撤销"}
                </Button>
              </div>
            </Dialog.Popup>
          </Dialog.Viewport>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  )
}
