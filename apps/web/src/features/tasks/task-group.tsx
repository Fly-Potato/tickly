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
  const rowProps = {
    timeZone,
    onSelect,
    onStatusChange,
  }

  return (
    <tbody
      className="task-group"
      data-context-only={group.context_only || undefined}
    >
      <TaskRow
        task={group.task}
        statusMutating={statusMutatingTaskIds.has(group.task.id)}
        progress={
          group.child_count > 0
            ? {
                completed: group.completed_child_count,
                total: group.child_count,
              }
            : undefined
        }
        contextOnly={group.context_only}
        {...rowProps}
      />
      {group.children.map((child) => (
        <TaskRow
          key={child.id}
          task={child}
          child
          statusMutating={statusMutatingTaskIds.has(child.id)}
          {...rowProps}
        />
      ))}
    </tbody>
  )
}
