import { useEffect, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { listRunLogs } from "@/lib/api";
import type { RunLog } from "@/types/config";

const variant = {
  success: "success",
  failed: "failed",
  running: "running",
  disabled: "disabled",
} as const;

export function RunLogDrawer({ open, logs, onClose, onLogsChange }: { open: boolean; logs: RunLog[]; onClose: () => void; onLogsChange?: (logs: RunLog[]) => void }) {
  const [displayLogs, setDisplayLogs] = useState(logs);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState("");

  useEffect(() => {
    setDisplayLogs(logs);
  }, [logs]);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    let timer: number | undefined;

    const refresh = async () => {
      setRefreshing(true);
      try {
        const nextLogs = await listRunLogs();
        if (!cancelled) {
          setDisplayLogs(nextLogs);
          onLogsChange?.(nextLogs);
          setLastRefreshAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
        }
      } finally {
        if (!cancelled) {
          setRefreshing(false);
        }
      }
    };

    void refresh();
    timer = window.setInterval(() => void refresh(), 3000);

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [open, onLogsChange]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end bg-black/35 backdrop-blur-sm">
      <div className="mx-auto mb-3 flex max-h-[70dvh] w-[min(980px,calc(100vw-24px))] flex-col rounded-2xl border border-border bg-card shadow-2xl sm:mb-6 sm:max-h-[52vh] sm:w-[min(980px,calc(100vw-32px))]">
        <div className="flex flex-col gap-3 border-b border-border px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div className="min-w-0">
            <div className="text-lg font-black">最近运行状态和日志</div>
            <div className="text-xs text-muted-foreground">
              显示后端操作结果、测试运行输出和 Prefect 发布输出，打开后每 3 秒自动刷新。
              {lastRefreshAt && <span className="ml-2">上次刷新 {lastRefreshAt}</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={refreshing ? "running" : "outline"}>
              {refreshing ? "刷新中" : "自动刷新"}
            </Badge>
            <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
          </div>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4 sm:p-6">
          {displayLogs.map((log) => {
            const displayLog = normalizeLogForDisplay(log);
            return (
              <div key={log.id} className="rounded-2xl border border-border bg-muted/20 p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="font-black">{displayLog.title}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{displayLog.message}</div>
                  </div>
                  <Badge variant={variant[displayLog.status]}>{displayLog.status}</Badge>
                </div>
                <div className="mt-3 font-mono text-xs text-muted-foreground">{displayLog.createdAt}</div>
                {displayLog.details && (
                  <pre className="mt-3 max-h-52 overflow-auto rounded-xl border border-border bg-slate-950 p-3 text-xs leading-5 text-slate-100">
                    {displayLog.details}
                  </pre>
                )}
              </div>
            );
          })}
          {!displayLogs.length && (
            <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              <RefreshCw className={`mx-auto mb-3 h-5 w-5 ${refreshing ? "animate-spin" : ""}`} />
              暂无运行日志。执行保存、测试运行或发布后会显示在这里。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function normalizeLogForDisplay(log: RunLog): RunLog {
  if (log.details || !log.message.includes("\n")) return log;

  const lines = log.message.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) return log;

  const summary = lines.find((line) => line.includes("RuntimeError(")) || lines[0];
  return {
    ...log,
    message: summary.length > 160 ? `${summary.slice(0, 157)}...` : summary,
    details: log.message,
  };
}
