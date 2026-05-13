import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { RunLog } from "@/types/config";

const variant = {
  success: "success",
  failed: "failed",
  running: "running",
  disabled: "disabled",
} as const;

export function RunLogDrawer({ open, logs, onClose }: { open: boolean; logs: RunLog[]; onClose: () => void }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end bg-black/35 backdrop-blur-sm">
      <div className="mx-auto mb-6 flex max-h-[52vh] w-[min(980px,calc(100vw-32px))] flex-col rounded-3xl border border-border bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div>
            <div className="text-lg font-black">最近运行状态和日志</div>
            <div className="text-xs text-muted-foreground">显示后端操作结果、测试运行输出和 Prefect 发布输出。</div>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto p-6">
          {logs.map((log) => (
            <div key={log.id} className="rounded-2xl border border-border bg-muted/20 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-black">{log.title}</div>
                  <div className="mt-1 text-sm text-muted-foreground">{log.message}</div>
                </div>
                <Badge variant={variant[log.status]}>{log.status}</Badge>
              </div>
              <div className="mt-3 font-mono text-xs text-muted-foreground">{log.createdAt}</div>
              {log.details && (
                <pre className="mt-3 max-h-52 overflow-auto rounded-xl border border-border bg-slate-950 p-3 text-xs leading-5 text-slate-100">
                  {log.details}
                </pre>
              )}
            </div>
          ))}
          {!logs.length && (
            <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              暂无运行日志。执行保存、测试运行或发布后会显示在这里。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
