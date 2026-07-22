import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeftRight,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Wifi,
  WifiOff,
} from "lucide-react";
import { EMPTY_FILTERS, createMockMonitorStream, filterRuns, getMonitorSnapshot, getSummary } from "./mockMonitorService";
import { PendingQueueDrawer } from "./PendingQueueDrawer";
import { RunDetailDrawer } from "./RunDetailDrawer";
import { StatusSummaryDrawer } from "./StatusSummaryDrawer";
import { apiMonitorService } from "./apiMonitorService";
import { formatMonitorAge, formatMonitorDateTime, formatMonitorTime } from "./time";
import { presentMonitorCurrentStep } from "./failurePresentation";
import type {
  MonitorFilters,
  MonitorRun,
  MonitorSnapshot,
  MonitorStreamMessage,
  PendingQueue,
  RunStatus,
} from "./types";
import "./monitor.css";

type ConnectionState = "loading" | "connected" | "disconnected" | "error";
export interface MonitorService {
  getSnapshot: () => Promise<MonitorSnapshot>;
  getRunDetail?: (runId: string) => Promise<MonitorRun>;
  createStream: (onUpdate: (update: MonitorStreamMessage) => void) => () => void;
}

const defaultService: MonitorService = import.meta.env.VITE_MONITOR_DATA_SOURCE === "mock"
  ? { getSnapshot: getMonitorSnapshot, createStream: createMockMonitorStream }
  : apiMonitorService;

const labels: Record<RunStatus, string> = { scheduled: "待执行", running: "运行中", succeeded: "成功", failed: "失败", skipped: "已跳过" };
const historyStatuses = ["succeeded", "running", "failed"] as const;
const triggerLabels = { all: "全部", scheduled: "定时调度", session: "每 10 分钟", manual: "手动试跑", web: "网页操作" } as const;
const RECORD_PAGE_SIZES = [10, 20, 50] as const;

function shortTime(value?: string) { return formatMonitorTime(value); }
function dayLabel(value?: string) { return value ? formatMonitorDateTime(value).slice(0, 10) : "未计划"; }
function duration(seconds?: number) { if (seconds === undefined) return "—"; return seconds >= 60 ? `${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2, "0")}秒` : `${seconds}秒`; }
function referenceTime(run: MonitorRun) { return run.startedAt ?? run.scheduledAt; }

function StatusPill({ status }: { status: RunStatus }) { return <span className={`status-pill status-${status}`}><i />{labels[status]}</span>; }

export function MonitorCenter({ service = defaultService }: { service?: MonitorService }) {
  const [runs, setRuns] = useState<MonitorRun[]>([]);
  const [pendingQueue, setPendingQueue] = useState<PendingQueue | null>(null);
  const [filters, setFilters] = useState<MonitorFilters>(EMPTY_FILTERS);
  const [updatedAt, setUpdatedAt] = useState("");
  const [upstream, setUpstream] = useState<MonitorSnapshot["upstream"] | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("loading");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<MonitorRun | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pendingQueueOpen, setPendingQueueOpen] = useState(false);
  const [summaryStatus, setSummaryStatus] = useState<Exclude<RunStatus, "scheduled" | "skipped"> | null>(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const [recordPage, setRecordPage] = useState(1);
  const [recordPageSize, setRecordPageSize] = useState<number>(RECORD_PAGE_SIZES[0]);
  const [timelineDayExpanded, setTimelineDayExpanded] = useState<Record<string, boolean>>({});
  const detailRequestId = useRef(0);


  useEffect(() => {
    setRecordPage(1);
  }, [filters]);
  useEffect(() => {
    let current = true;
    let stopStream: () => void = () => {};
    setConnection("loading");
    service.getSnapshot().then((snapshot) => {
      if (!current) return;
      setRuns(snapshot.runs);
      setPendingQueue(snapshot.pendingQueue);
      setUpdatedAt(snapshot.updatedAt);
      setConnection(snapshot.connected ? "connected" : "disconnected");
      setUpstream(snapshot.upstream ?? null);
      // 详情只在用户点击记录后打开，关闭抽屉不会留下右侧占位。
      setSelectedId(null);
      setSelectedDetail(null);
      setDetailLoading(false);
      stopStream = service.createStream((update) => {
        if (!current) return;
        if (update.type === "connection") {
          setConnection(update.connected ? "connected" : "disconnected");
          return;
        }
        if (update.type === "snapshot") {
          setRuns(update.runs);
        } else {
          setRuns((previous) => {
            const withoutChanged = previous.filter((run) => run.id !== update.runId);
            return update.run ? [...withoutChanged, update.run] : withoutChanged;
          });
        }
        setPendingQueue(update.pendingQueue);
        setUpdatedAt(update.updatedAt);
        setConnection(update.connected ? "connected" : "disconnected");
        setUpstream(update.upstream);
      });
    }).catch(() => { if (current) setConnection("error"); });
    return () => { current = false; stopStream(); };
  }, [service, loadVersion]);

  const referenceNow = useMemo(() => {
    const parsed = new Date(updatedAt);
    return Number.isFinite(parsed.getTime()) ? parsed : new Date();
  }, [updatedAt]);
  const historyRuns = useMemo(() => filterRuns(runs, EMPTY_FILTERS, referenceNow), [referenceNow, runs]);
  const reportPendingQueue = useMemo<PendingQueue | null>(() => {
    if (!pendingQueue) return null;
    const items = pendingQueue.items.filter((item) => item.targetId.startsWith("report-"));
    return { scopeLabel: "通报当前 Scheduled", total: items.length, items };
  }, [pendingQueue]);
  const filteredRuns = useMemo(() => filterRuns(runs, filters, referenceNow).sort((a, b) => (referenceTime(b) ?? "").localeCompare(referenceTime(a) ?? "")), [filters, referenceNow, runs]);
  const summary = useMemo(() => getSummary(filteredRuns), [filteredRuns]);
  const selectedRun = runs.find((run) => run.id === selectedId) ?? null;
  const displayedRun = selectedDetail ?? selectedRun;
  const todayLabel = formatMonitorDateTime(referenceNow.toISOString()).slice(0, 10);
  const targetOptions = useMemo(() => Array.from(new Map(historyRuns.map((run) => [run.targetId, run.target])).entries()), [historyRuns]);
  const hasFilters = filters.target !== "all" || filters.trigger !== "all" || filters.status !== "all" || Boolean(filters.startAt || filters.endAt);
  const recordPageCount = Math.max(1, Math.ceil(filteredRuns.length / recordPageSize));
  const currentRecordPage = Math.min(recordPage, recordPageCount);
  const pagedTableRuns = useMemo(() => {
    const start = (currentRecordPage - 1) * recordPageSize;
    return filteredRuns.slice(start, start + recordPageSize);
  }, [currentRecordPage, filteredRuns, recordPageSize]);
  const groupedTimeline = useMemo(() => filteredRuns.reduce<Record<string, MonitorRun[]>>((groups, run) => { const key = dayLabel(referenceTime(run)); (groups[key] ??= []).push(run); return groups; }, {}), [filteredRuns]);

  const updateFilter = <K extends keyof MonitorFilters>(key: K, value: MonitorFilters[K]) => setFilters((current) => ({ ...current, [key]: value }));
  const toggleTimelineDay = (date: string) => setTimelineDayExpanded((current) => ({ ...current, [date]: !(current[date] ?? date === todayLabel) }));
  const refresh = () => setLoadVersion((version) => version + 1);
  const openStatusSummary = (status: Exclude<RunStatus, "scheduled" | "skipped">) => { setSelectedId(null); setPendingQueueOpen(false); setSummaryStatus(status); };
  const openRunDetail = (run: MonitorRun) => { setPendingQueueOpen(false); setSummaryStatus(null); setSelectedId(run.id); setSelectedDetail(run); if (run.source !== "prefect" || !service.getRunDetail) { setDetailLoading(false); return; } const requestId = detailRequestId.current + 1; detailRequestId.current = requestId; setDetailLoading(true); service.getRunDetail(run.id).then((detail) => { if (detailRequestId.current === requestId) setSelectedDetail(detail); }).catch(() => { if (detailRequestId.current === requestId) setSelectedDetail({ ...run, detailAvailable: false, detailMessage: "Prefect 详情暂不可用，正在显示已同步摘要。" }); }).finally(() => { if (detailRequestId.current === requestId) setDetailLoading(false); }); };

  return <main className="monitor-page">
    <header className="monitor-header">
      <div><h1>运行监控中心</h1><p>自动通报 · 最近 30 天运行概览</p></div>
      <div className="header-actions">
        <span className={`connection ${connection}`}>{connection === "connected" ? <Wifi size={15} /> : connection === "loading" ? <LoaderCircle className="spin" size={15} /> : <WifiOff size={15} />}{connection === "connected" ? "页面实时：已连接" : connection === "loading" ? "页面实时：正在连接" : connection === "error" ? "页面实时：连接异常" : "页面实时：已断开"}</span>
        <span className="update-time">数据快照：{formatMonitorDateTime(updatedAt)}</span>
        <span className="upstream-state">上游事件：{upstream?.lastAcceptedAt ? `${formatMonitorDateTime(upstream.lastAcceptedAt)}${formatMonitorAge(upstream.lastAcceptedAt, referenceNow) ? `（${formatMonitorAge(upstream.lastAcceptedAt, referenceNow)}）` : ""}` : "尚未收到"}</span>
        <span className="upstream-state">最近对账：{upstream?.lastReconciledAt ? `${formatMonitorDateTime(upstream.lastReconciledAt)}${formatMonitorAge(upstream.lastReconciledAt, referenceNow) ? `（${formatMonitorAge(upstream.lastReconciledAt, referenceNow)}）` : ""}` : "尚未对账"}</span>
        {upstream?.lastErrorCategory && <span className="upstream-error">上游异常：{upstream.lastErrorCategory}</span>}
        <button className="refresh-button" onClick={refresh}><RefreshCw size={16} />刷新</button>
      </div>
    </header>

    <section className="workbar monitor-panel" aria-label="运行筛选">
      <div className="filter-area">
        <label>运行对象<select aria-label="运行对象" value={filters.target} onChange={(event) => updateFilter("target", event.target.value)}><option value="all">全部</option>{targetOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><ChevronDown aria-hidden="true" size={15} /></label>
        <label>触发方式<select aria-label="触发方式" value={filters.trigger} onChange={(event) => updateFilter("trigger", event.target.value as MonitorFilters["trigger"])}>{Object.entries(triggerLabels).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select><ChevronDown aria-hidden="true" size={15} /></label>
        <label>状态<select aria-label="状态" value={filters.status} onChange={(event) => updateFilter("status", event.target.value as MonitorFilters["status"])}><option value="all">全部</option>{historyStatuses.map((status) => <option key={status} value={status}>{labels[status]}</option>)}</select><ChevronDown aria-hidden="true" size={15} /></label>
        <label>开始时间<span className="date-input"><CalendarDays size={15} /><input aria-label="开始时间" type="date" value={filters.startAt} onChange={(event) => updateFilter("startAt", event.target.value ? `${event.target.value}T00:00:00+08:00` : "")} /></span></label>
        <label>结束时间<span className="date-input"><CalendarDays size={15} /><input aria-label="结束时间" type="date" value={filters.endAt} onChange={(event) => updateFilter("endAt", event.target.value ? `${event.target.value}T23:59:59+08:00` : "")} /></span></label>
        <button className="reset-button" onClick={() => setFilters(EMPTY_FILTERS)}><RotateCcw size={16} />重置</button>
      </div>
    </section>

    {connection === "error" ? <section className="monitor-panel load-state"><AlertCircle size={24} /><strong>监控数据暂时不可用</strong><p>监控服务未能响应；请稍后刷新页面。</p><button className="refresh-button" onClick={refresh}>重新加载</button></section> : <div className={`monitor-grid ${pendingQueueOpen || summaryStatus || displayedRun ? "has-detail" : ""}`}>
      <section className="timeline-panel monitor-panel" aria-label="运行时间线">
        <div className="panel-title"><div><h2>运行时间线</h2><p>按实际开始或计划时间排序</p></div></div>
        {connection === "loading" ? <div className="skeleton-list"><i /><i /><i /><i /></div> : filteredRuns.length === 0 ? <div className="empty-state"><CalendarDays size={24} /><p>这个条件下没有运行记录</p><button onClick={() => setFilters(EMPTY_FILTERS)}>清除筛选</button></div> : <div className="timeline-groups" tabIndex={0} aria-label="运行时间线记录，可向下滚动查看全部记录">
          {Object.entries(groupedTimeline).map(([date, list]) => {
            const expanded = timelineDayExpanded[date] ?? date === todayLabel;
            const actionLabel = `${expanded ? "收起" : "展开"} ${date}`;
            const failedCount = list.filter((run) => run.status === "failed").length;
            const daySummary = failedCount ? `${list.length} 次 · ${failedCount} 失败` : `${list.length} 次`;
            return <div className="timeline-day" key={date}>
              <div className="timeline-day-heading"><h3>{date}{date === todayLabel && <em>（今天）</em>}</h3><span className="timeline-day-summary">{daySummary}</span><button className="timeline-day-toggle" aria-label={actionLabel} aria-expanded={expanded} title={actionLabel} onClick={() => toggleTimelineDay(date)}>{expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button></div>
              {expanded && list.map((run) => <button key={run.id} className={`timeline-item ${selectedId === run.id ? "selected" : ""}`} onClick={() => openRunDetail(run)}><span className={`timeline-marker status-${run.status}`} /> <div><time>{shortTime(referenceTime(run))}</time><strong>{run.target}</strong></div><small>{run.status === "scheduled" ? "待执行" : duration(run.durationSeconds)}</small></button>)}
            </div>;
          })}
        </div>}
      </section>

      <section className="record-panel monitor-panel" aria-label="运行记录">
        <div className="panel-title record-title"><div><h2>运行记录 <span>· {hasFilters ? "筛选结果" : "全部运行记录"}</span></h2><p>{hasFilters ? "已应用筛选条件" : "最近 30 天"} · 共 {filteredRuns.length} 次</p></div><div className="record-header-actions"><div className="record-overview" aria-label="运行概况"><button aria-label={`成功 ${summary.succeeded}`} onClick={() => openStatusSummary("succeeded")}><CheckCircle2 size={15} /><span>成功</span><b>{summary.succeeded}</b></button><button aria-label={`运行中 ${summary.running}`} onClick={() => openStatusSummary("running")}><LoaderCircle size={15} /><span>运行中</span><b>{summary.running}</b></button><button aria-label={`失败 ${summary.failed}`} onClick={() => openStatusSummary("failed")}><AlertCircle size={15} /><span>失败</span><b>{summary.failed}</b></button><button className="pending-queue-button" aria-label={`待执行（全局） ${reportPendingQueue?.total ?? 0}，${reportPendingQueue?.scopeLabel ?? "通报当前 Scheduled"}`} onClick={() => { setSelectedId(null); setSummaryStatus(null); setPendingQueueOpen(true); }}><Clock3 size={15} /><span>待执行（全局）</span><b>{reportPendingQueue?.total ?? 0}</b></button></div><span className="count-note">点击记录查看详情</span></div></div>
        <p className="table-scroll-hint" aria-hidden="true"><ArrowLeftRight size={14} />左右滑动查看全部列</p>
        <div className="table-wrap" tabIndex={0} aria-label="运行记录表格，可左右滑动查看全部列"><table><thead><tr><th>运行对象</th><th>触发方式</th><th>状态</th><th>计划/开始时间</th><th>当前环节</th><th>耗时</th></tr></thead><tbody>
          {connection === "loading" ? Array.from({ length: 6 }).map((_, index) => <tr className="skeleton-row" key={index}><td colSpan={6}><i /></td></tr>) : pagedTableRuns.map((run) => <tr key={run.id} className={selectedId === run.id ? "selected" : ""} tabIndex={0} onClick={() => openRunDetail(run)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") openRunDetail(run); }}><td><strong>{run.target}</strong><small className="source-label">{run.source === "prefect" ? "调度服务" : "网页操作"}</small></td><td>{triggerLabels[run.trigger]}</td><td><StatusPill status={run.status} /></td><td>{formatMonitorDateTime(referenceTime(run))}</td><td>{presentMonitorCurrentStep(run.currentStep, run.error)}</td><td>{duration(run.durationSeconds)}</td></tr>)}
        </tbody></table></div>
        <div className="record-pagination" aria-label="运行记录分页">
          <label>每页显示<select aria-label="每页显示" value={recordPageSize} onChange={(event) => { setRecordPageSize(Number(event.target.value)); setRecordPage(1); }}>{RECORD_PAGE_SIZES.map((size) => <option key={size} value={size}>{size} 条</option>)}</select></label>
          <span>第 {currentRecordPage} / {recordPageCount} 页</span>
          <div className="record-pagination-actions"><button className="pagination-icon-button" aria-label="上一页" title="上一页" disabled={currentRecordPage === 1} onClick={() => setRecordPage((page) => Math.max(1, page - 1))}><ChevronLeft size={16} /></button><button className="pagination-icon-button" aria-label="下一页" title="下一页" disabled={currentRecordPage === recordPageCount} onClick={() => setRecordPage((page) => Math.min(recordPageCount, page + 1))}><ChevronRight size={16} /></button></div>
        </div>
      </section>

      {pendingQueueOpen && reportPendingQueue && <PendingQueueDrawer queue={reportPendingQueue} onClose={() => setPendingQueueOpen(false)} />}
      {summaryStatus && !pendingQueueOpen && <StatusSummaryDrawer status={summaryStatus} runs={filteredRuns.filter((run) => run.status === summaryStatus)} onClose={() => setSummaryStatus(null)} onSelectRun={openRunDetail} />}
      {!pendingQueueOpen && !summaryStatus && displayedRun && <RunDetailDrawer run={displayedRun} isLoadingDetails={detailLoading} onClose={() => { detailRequestId.current += 1; setSelectedId(null); setSelectedDetail(null); setDetailLoading(false); }} />}
    </div>}
  </main>;
}
