import { LogOut } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import type { Task, TaskUpdateInput } from "./task-api"
import { TaskEditorPanel } from "./task-editor-panel"
import { TaskList } from "./task-list"
import { TaskToolbar } from "./task-toolbar"
import { QuickCreateForm } from "./quick-create-form"
import { useTaskWorkspace } from "./use-task-workspace"

type TodoWorkspaceProps = {
  username: string
  timeZone: string
  loggingOut: boolean
  onLogout(): Promise<void>
}

/** 组合 Todo 单页工作区；只协调局部组件，不把任务状态提升到认证 Context。 */
export function TodoWorkspace({
  username,
  timeZone,
  loggingOut,
  onLogout,
}: TodoWorkspaceProps) {
  const { state, actions } = useTaskWorkspace()
  const [editorDirty, setEditorDirty] = useState(false)
  const [editorError, setEditorError] = useState<string | null>(null)
  const selectedTask =
    state.selectedTaskId === null
      ? null
      : (state.items.find((task) => task.id === state.selectedTaskId) ?? null)

  function selectTask(task: Task) {
    if (editorDirty && !window.confirm("放弃未保存的修改？")) {
      return
    }
    setEditorDirty(false)
    setEditorError(null)
    actions.selectTask(task.id)
  }

  function closeEditor() {
    setEditorDirty(false)
    setEditorError(null)
    actions.selectTask(null)
  }

  async function saveTask(patch: TaskUpdateInput) {
    if (selectedTask === null) return
    setEditorError(null)
    try {
      await actions.save(selectedTask.id, patch)
      setEditorDirty(false)
    } catch (error) {
      setEditorError(safeErrorMessage(error, "任务保存失败"))
      throw error
    }
  }

  async function deleteSelectedTask() {
    if (selectedTask === null) return
    setEditorError(null)
    try {
      await actions.remove(selectedTask.id)
      setEditorDirty(false)
    } catch (error) {
      setEditorError(safeErrorMessage(error, "任务删除失败"))
      throw error
    }
  }

  return (
    <main className="todo-page">
      <section className="todo-shell" aria-labelledby="workspace-title">
        <header className="todo-header">
          <div className="auth-brand-row">
            <span className="auth-brand-mark">T</span>
            <div>
              <span className="auth-brand-name">Tickly</span>
              <p className="todo-brand-note">Personal cadence</p>
            </div>
          </div>
          <div className="todo-account">
            <div>
              <strong>{username}</strong>
              <span>{timeZone}</span>
            </div>
            <Button
              type="button"
              variant="outline"
              disabled={loggingOut}
              onClick={() => void onLogout()}
            >
              <LogOut aria-hidden="true" />
              {loggingOut ? "正在退出" : "退出登录"}
            </Button>
          </div>
        </header>

        <div className="todo-hero">
          <p className="auth-card-index">Today / Queue</p>
          <h1 id="workspace-title">今天要完成什么？</h1>
          <p>先记录，再安排节奏。所有任务都会同步到当前账号。</p>
        </div>

        <QuickCreateForm creating={state.creating} onCreate={actions.create} />
        <TaskToolbar
          status={state.query.status}
          sort={state.query.sort}
          order={state.query.order}
          disabled={state.initialLoading}
          onStatusChange={actions.setStatus}
          onSortChange={actions.setSort}
          onOrderChange={actions.setOrder}
        />
        <TaskList
          tasks={state.items}
          status={state.query.status}
          timeZone={timeZone}
          initialLoading={state.initialLoading}
          loadingMore={state.loadingMore}
          nextCursor={state.nextCursor}
          error={state.error}
          completionError={state.completionError}
          completingTaskIds={state.completingTaskIds}
          onRetry={actions.retry}
          onLoadMore={actions.loadMore}
          onSelect={selectTask}
          onCompletedChange={actions.setCompleted}
        />
      </section>

      {selectedTask !== null ? (
        <TaskEditorPanel
          key={selectedTask.id}
          task={selectedTask}
          timeZone={timeZone}
          saving={state.saving}
          deleting={state.deleting}
          error={editorError}
          onDirtyChange={setEditorDirty}
          onSave={saveTask}
          onDelete={deleteSelectedTask}
          onClose={closeEditor}
        />
      ) : null}
    </main>
  )
}
