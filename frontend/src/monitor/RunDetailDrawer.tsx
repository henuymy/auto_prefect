import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Clock3,
  Info,
  LoaderCircle,
  X,
} from "lucide-react";
import type { MonitorRun, RunStatus } from "./types";
import { presentMonitorFailure } from "./failurePresentation";
import { formatMonitorDateTime, formatMonitorTime } from "./time";
import { useDrawerDialog } from "./useDrawerDialog";

type DetailTab = "progress" | "logs" | "error";

const statusLabel: Record<RunStatus, string> = {
  scheduled: "待执行",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  skipped: "已跳过",
};

function formatDuration(seconds?: number) {
  if (seconds === undefined) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}分${String(rest).padStart(2, "0")}秒` : `${rest}秒`;
}

function StepIcon({ status }: { status: MonitorRun["steps"][number]["status"] }) {
  if (status === "completed") return <CheckCircle2 aria-hidden="true" />;
  if (status === "failed") return <AlertCircle aria-hidden="true" />;
  if (status === "active") return <LoaderCircle aria-hidden="true" className="spin" />;
  return <Circle aria-hidden="true" />;
}

export function RunDetailDrawer({ run, onClose, isLoadingDetails = false }: { run: MonitorRun; onClose: () => void; isLoadingDetails?: boolean }) {
  const { dialogRef, closeButtonRef } = useDrawerDialog(onClose);
  const [tab, setTab] = useState<DetailTab>("progress");
  const [technical, setTechnical] = useState(false);
  const [level, setLevel] = useState<"all" | "INFO" | "WARN" | "ERROR">("all");
  const visibleLogs = useMemo(() => run.logs.filter((item) => level === "all" || item.level === level), [level, run.logs]);
  const isFailure = run.status === "failed";
  const failure = isFailure && run.error ? presentMonitorFailure(run.error) : undefined;

  return (
    <aside ref={dialogRef} className="monitor-detail" role="dialog" aria-modal="true" aria-label="运行详情">
      <div className="detail-heading">
        <div>
          <p className="detail-overline">运行详情</p>
          <h2>{run.target}</h2>
          <div className="detail-meta">
            {technical && <span>{run.id}</span>}<span className={`status-dot status-${run.status}`}>{statusLabel[run.status]}</span>
          </div>
        </div>
        <button ref={closeButtonRef} className="icon-button" aria-label="关闭运行详情" onClick={onClose}><X size={18} /></button>
      </div>

      <div className="detail-facts">
        <span><small>来源</small>{run.source === "prefect" ? "调度服务" : "网页操作"}</span>
        <span><small>开始时间</small>{formatMonitorDateTime(run.startedAt ?? run.scheduledAt)}</span>
        <span><small>耗时</small>{formatDuration(run.durationSeconds)}</span>
      </div>

      <div className="detail-controls">
        <div className="detail-tabs" role="tablist" aria-label="运行详情标签页">
          {(["progress", "logs", "error"] as DetailTab[]).map((item) => (
            <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
              {{ progress: "进度", logs: "日志", error: "异常" }[item]}
            </button>
          ))}
        </div>
        <div className="mode-switch" role="group" aria-label="信息层级">
          <button aria-pressed={!technical} className={!technical ? "active" : ""} onClick={() => setTechnical(false)}>业务概览</button>
          <button aria-pressed={technical} className={technical ? "active" : ""} onClick={() => setTechnical(true)}>技术详情</button>
        </div>
      </div>

      {isLoadingDetails && <p className="detail-notice">正在从 Prefect 获取运行详情。</p>}
      {run.detailAvailable === false && <p className="detail-unavailable">{run.detailMessage}</p>}

      {tab === "progress" && (
        <section className="detail-section" aria-label="运行进度">
          <h3>运行进度</h3>
          {failure && <section className="failure-summary" aria-label="失败摘要">
            <h4>失败摘要</h4>
            <p>{failure.businessSummary}</p>
            <span>失败步骤：{failure.failedStep}</span>
          </section>}
          <div className="step-list">
            {run.steps.map((step) => (
              <div className={`step-row step-${step.status}`} key={step.name}>
                <span className="step-icon"><StepIcon status={step.status} /></span>
                <div><strong>{step.name}</strong><p>{technical ? step.message : step.status === "failed" ? step.message : step.status === "active" ? "正在处理" : step.status === "completed" ? "已完成" : "尚未开始"}</p></div>
                <time>{formatMonitorTime(step.finishedAt ?? step.startedAt)}</time>
              </div>
            ))}
            {run.steps.length === 0 && <p className="empty-inline">暂无可用步骤</p>}
          </div>
        </section>
      )}

      {tab === "logs" && (
        <section className="detail-section" aria-label="运行日志">
          <div className="section-row"><h3>运行日志</h3><select aria-label="日志级别" value={level} onChange={(event) => setLevel(event.target.value as typeof level)}><option value="all">全部</option><option value="INFO">INFO</option><option value="WARN">WARN</option><option value="ERROR">ERROR</option></select></div>
          <div className="log-list">
          {visibleLogs.map((log, index) => <div className={`log-row log-${log.level.toLowerCase()}`} key={`${log.at}-${index}`}><time>{formatMonitorTime(log.at)}</time><b>{log.level}</b><span>{log.message}</span></div>)}
            {visibleLogs.length === 0 && <p className="empty-inline">暂无符合条件的日志</p>}
          </div>
          <p className="safe-note"><Info size={14} />日志已按内部安全规范脱敏。</p>
        </section>
      )}

      {tab === "error" && (
        <section className="detail-section" aria-label="异常信息">
          {failure ? (
            <div className="error-card">
              <h3>异常信息</h3>
              <dl>
                <div><dt>失败步骤</dt><dd>{failure.failedStep}</dd></div>
                <div><dt>异常分类</dt><dd>{failure.category}</dd></div>
                <div><dt>业务摘要</dt><dd className="danger-text">{failure.businessSummary}</dd></div>
                {technical && <div><dt>技术摘要</dt><dd>{failure.technicalSummary}</dd></div>}
                {technical && <div><dt>重试次数</dt><dd>{failure.retryCount} 次</dd></div>}
              </dl>
            </div>
          ) : <div className="empty-state compact"><CheckCircle2 size={23} /><p>本次运行未发现异常</p></div>}
        </section>
      )}
    </aside>
  );
}
