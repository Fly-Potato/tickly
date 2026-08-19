import { LogOut, Plus, X } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { safeErrorMessage } from "@/lib/api-error"
import {
  DEFAULT_TASK_QUERY,
  type ParentTaskOption,
  type Task,
  type TaskStatusFilter,
  type TaskUpdateInput,
} from "./task-api"
import { MobileTaskFilterDialog } from "./mobile-task-filter-dialog"
import { TaskCreatePanel } from "./task-create-panel"
import { TaskEditorPanel } from "./task-editor-panel"
import { TaskFilterSidebar } from "./task-filter-sidebar"
import { TaskList } from "./task-list"
import { findTaskInGroups, useTaskWorkspace } from "./use-task-workspace"

type TodoWorkspaceProps = {
  username: string
  timeZone: string
  loggingOut: boolean
  onLogout(): Promise<void>
}

type WorkspaceHeaderProps = Pick<
  TodoWorkspaceProps,
  "username" | "timeZone" | "loggingOut" | "onLogout"
>

const statusLabels: Record<TaskStatusFilter, string> = {
  all: "All",
  new: "New",
  in_progress: "In Progress",
  completed: "Completed",
}

function WorkspaceHeader({
  username,
  timeZone,
  loggingOut,
  onLogout,
}: WorkspaceHeaderProps) {
  return (
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
  )
}

type ActiveFilterSummaryProps = {
  status: TaskStatusFilter
  topic: string | undefined
  onClearTopic(): void
}

function ActiveFilterSummary({
  status,
  topic,
  onClearTopic,
}: ActiveFilterSummaryProps) {
  const hasActiveFilter = status !== "all" || topic !== undefined
  const summary = [
    status === "all" ? null : statusLabels[status],
    topic ?? null,
  ].filter((value): value is string => value !== null)
  const summaryText = summary.join(" · ")

  return (
    <>
      <span
        className="sr-only"
        role="status"
        aria-label="筛选变化"
        aria-live="polite"
        aria-atomic="true"
      >
        当前筛选：{hasActiveFilter ? summaryText : "无"}
      </span>
      {hasActiveFilter ? (
        <section className="active-filter-summary" aria-label="当前筛选">
          <span>{summaryText}</span>
          {topic !== undefined ? (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`清除主题筛选 ${topic}`}
              onClick={onClearTopic}
            >
              <X aria-hidden="true" />
            </Button>
          ) : null}
        </section>
      ) : null}
    </>
  )
}

/** 组合 Todo 单页工作区；只协调局部组件，不把任务状态提升到认证 Context。 */
export function TodoWorkspace({
  username,
  timeZone,
  loggingOut,
  onLogout,
}: TodoWorkspaceProps) {
  const { state, actions } = useTaskWorkspace()
  const [createOpen, setCreateOpen] = useState(false)
  const [editorDirty, setEditorDirty] = useState(false)
  const [editorError, setEditorError] = useState<string | null>(null)
  const selectedTask =
    state.selectedTaskId === null
      ? null
      : findTaskInGroups(state.items, state.selectedTaskId)
  const selectedTaskGroup =
    state.selectedTaskId === null
      ? null
      : (state.items.find(
          (group) =>
            group.task.id === state.selectedTaskId ||
            group.children.some((child) => child.id === state.selectedTaskId)
        ) ?? null)
  // 删除提示使用服务端分组计数；子任务即使位于父分组中也没有自己的子待办。
  const selectedTaskChildCount =
    selectedTask !== null &&
    selectedTask.parent_id === null &&
    selectedTaskGroup?.task.id === selectedTask.id
      ? selectedTaskGroup.child_count
      : 0
  const selectedTaskParent: ParentTaskOption | null =
    selectedTask !== null &&
    selectedTask.parent_id !== null &&
    selectedTaskGroup !== null &&
    selectedTaskGroup.task.id !== selectedTask.id
      ? {
          id: selectedTaskGroup.task.id,
          serial: selectedTaskGroup.task.serial,
          title: selectedTaskGroup.task.title,
          topic: selectedTaskGroup.task.topic,
          status: selectedTaskGroup.task.status,
        }
      : null
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
        <WorkspaceHeader
          username={username}
          timeZone={timeZone}
          loggingOut={loggingOut}
          onLogout={onLogout}
        />

        <div className="todo-workspace-layout">
          <TaskFilterSidebar
            query={state.query}
            topics={state.topics}
            disabled={state.initialLoading}
            topicLoading={state.topicLoading}
            topicError={state.topicError}
            onStatusChange={actions.setStatus}
            onTopicChange={actions.setTopic}
            onSortChange={actions.setSort}
            onOrderChange={actions.setOrder}
            onRetryTopics={actions.retryTopics}
            onReset={() => actions.applyQuery({ ...DEFAULT_TASK_QUERY })}
          />
          <section className="task-list-content" aria-label="Todo List">
            <div className="task-list-toolbar">
              <h1 id="workspace-title" className="auth-card-index">
                Todo list
              </h1>
              <div className="task-list-toolbar-actions">
                <MobileTaskFilterDialog
                  query={state.query}
                  topics={state.topics}
                  disabled={state.initialLoading}
                  topicLoading={state.topicLoading}
                  topicError={state.topicError}
                  onRetryTopics={actions.retryTopics}
                  onApply={actions.applyQuery}
                />
                <Button
                  type="button"
                  disabled={state.creating}
                  onClick={() => setCreateOpen(true)}
                >
                  <Plus aria-hidden="true" />
                  新建待办
                </Button>
              </div>
            </div>

            <ActiveFilterSummary
              status={state.query.status}
              topic={state.query.topic}
              onClearTopic={() => actions.setTopic(undefined)}
            />
            <TaskList
              groups={state.items}
              status={state.query.status}
              timeZone={timeZone}
              initialLoading={state.initialLoading}
              loadingMore={state.loadingMore}
              nextCursor={state.nextCursor}
              error={state.error}
              statusError={state.statusError}
              statusMutatingTaskIds={state.statusMutatingTaskIds}
              onRetry={actions.retry}
              onLoadMore={actions.loadMore}
              onSelect={selectTask}
              onStatusChange={actions.changeStatus}
            />
          </section>
        </div>
      </section>

      {createOpen ? (
        <TaskCreatePanel
          selectedTopic={state.query.topic}
          topicOptions={state.topics}
          timeZone={timeZone}
          creating={state.creating}
          onCreate={actions.create}
          onClose={() => setCreateOpen(false)}
        />
      ) : null}

      {selectedTask !== null ? (
        <TaskEditorPanel
          key={selectedTask.id}
          task={selectedTask}
          childCount={selectedTaskChildCount}
          currentParent={selectedTaskParent}
          timeZone={timeZone}
          saving={state.saving}
          deleting={state.deleting}
          creatingChild={state.creating}
          error={editorError}
          onDirtyChange={setEditorDirty}
          onSave={saveTask}
          onDelete={deleteSelectedTask}
          onCreateChild={actions.create}
          onClose={closeEditor}
        />
      ) : null}
    </main>
  )
}
