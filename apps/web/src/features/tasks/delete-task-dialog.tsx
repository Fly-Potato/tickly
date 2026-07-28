import { Dialog } from "@base-ui/react/dialog"

import { Button } from "@/components/ui/button"

type DeleteTaskDialogProps = {
  taskTitle: string
  open: boolean
  deleting: boolean
  onOpenChange(open: boolean): void
  onConfirm(): Promise<void>
}

export function DeleteTaskDialog({
  taskTitle,
  open,
  deleting,
  onOpenChange,
  onConfirm,
}: DeleteTaskDialogProps) {
  async function handleConfirm() {
    onOpenChange(false)
    try {
      await onConfirm()
    } catch {
      // 父编辑面板保留上下文并在面板内显示稳定错误，确认框无需叠加停留。
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="task-delete-backdrop fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
        <Dialog.Viewport className="fixed inset-0 z-50 grid place-items-center p-5">
          <Dialog.Popup className="w-full max-w-md rounded-2xl border border-border bg-card p-6 text-card-foreground shadow-2xl outline-none">
            <Dialog.Title className="text-xl font-semibold tracking-tight">
              删除任务？
            </Dialog.Title>
            <Dialog.Description className="mt-3 text-sm leading-6 text-muted-foreground">
              “{taskTitle}”将被永久删除，此操作无法撤销。
            </Dialog.Description>
            <div className="mt-7 flex justify-end gap-3">
              <Button
                type="button"
                variant="outline"
                disabled={deleting}
                onClick={() => onOpenChange(false)}
              >
                取消
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={deleting}
                onClick={() => void handleConfirm()}
              >
                {deleting ? "正在删除" : "确认删除"}
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
