import { CheckCircle2, CircleAlert, Clock3, LoaderCircle, X } from "lucide-react";
import type { MonitorRun, RunStatus } from "./types";
import { formatMonitorDateTime } from "./time";
import { useDrawerDialog } from "./useDrawerDialog";

const summaryConfig = {
  succeeded: { title: "成功运行", icon: CheckCircle2, description: "本次筛选条件下已完成的运行记录" },
  running: { title: "运行中", icon: LoaderCircle, description: "本次筛选条件下正在执行的运行记录" },
  failed: { title: "失败运行", icon: CircleAlert, description: "本次筛选条件下需要关注的失败记录" },
} as const;

function formatDuration(seconds?: number) { return seconds === undefined ? "—" : seconds >= 60 ? `${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2, "0")}秒` : `${seconds}秒`; }

export function StatusSummaryDrawer({ status, runs, onClose, onSelectRun }: { status: Exclude<RunStatus, "scheduled" | "skipped">; runs: MonitorRun[]; onClose: () => void; onSelectRun: (run: MonitorRun) => void }) {
  const { dialogRef, closeButtonRef } = useDrawerDialog(onClose);
  const config = summaryConfig[status];
  const Icon = config.icon;
  return <aside ref={dialogRef} className={`monitor-detail status-summary-drawer status-${status}`} role="dialog" aria-modal="true" aria-label={config.title}>
    <div className="detail-heading"><div><p className="detail-overline">状态汇总</p><h2>{config.title}</h2><div className="detail-meta"><span>{config.description}</span><span className={`status-dot status-${status}`}>{runs.length} 次</span></div></div><button ref={closeButtonRef} className="icon-button" aria-label={`关闭${config.title}`} onClick={onClose}><X size={18} /></button></div>
    <div className="queue-summary status-summary-total"><Icon size={19} /><div><strong>{runs.length}</strong><span>{status === "running" ? "条记录正在执行" : status === "failed" ? "条记录等待关注" : "条记录已完成"}</span></div></div>
    <section className="detail-section" aria-label={`${config.title}记录`}><div className="section-row"><h3>运行记录</h3><span className="queue-caption">点击查看详情</span></div><div className="queue-list">
      {runs.length ? runs.map((run) => <button className="summary-run-row" key={run.id} onClick={() => onSelectRun(run)}><div><strong>{run.target}</strong><p>{formatMonitorDateTime(run.startedAt ?? run.scheduledAt)} · {run.currentStep}</p></div><div><b>{formatDuration(run.durationSeconds)}</b><small>{run.source === "prefect" ? "调度服务" : "网页操作"}</small></div></button>) : <p className="empty-inline">当前筛选条件下没有记录</p>}
    </div></section>
  </aside>;
}
