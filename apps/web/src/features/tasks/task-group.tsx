import type { Task, TaskGroup, TaskStatus } from "./task-api"
import { TaskRow } from "./task-row"

type TaskGroupViewProps = {
  group: TaskGroup
  timeZone: string
  statusMutatingTaskIds: ReadonlySet<string>
  onSelect(task: Task): void
  onStatusChange(task: Task, status: TaskStatus): Promise<void>
}

export function TaskGroupView({
  group,
  timeZone,
  statusMutatingTaskIds,
  onSelect,
  onStatusChange,
}: TaskGroupViewProps) {
  const serial = group.task.serial
  const rowProps = {
    timeZone,
    onSelect,
    onStatusChange,
  }

  return (
    <article
      className="task-group"
      data-context-only={group.context_only || undefined}
    >
      <TaskRow
        task={group.task}
        statusMutating={statusMutatingTaskIds.has(group.task.id)}
        {...rowProps}
      />
      {group.context_only ? (
        <p className="task-context-note">仅用于展示匹配的子待办</p>
      ) : null}
      {group.children.length > 0 ? (
        <div className="child-task-section">
          <p>
            子待办 {group.completed_child_count}/{group.child_count} 已完成
          </p>
          <ul aria-label={`#${serial} 的子待办`}>
            {group.children.map((child) => (
              <li key={child.id}>
                <TaskRow
                  task={child}
                  statusMutating={statusMutatingTaskIds.has(child.id)}
                  {...rowProps}
                />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  )
}
