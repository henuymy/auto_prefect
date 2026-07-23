import type { MonitorFilters, MonitorRun, MonitorSnapshot, MonitorStreamMessage, MonitorSummary, PendingQueue, RunStatus } from "./types";

export const EMPTY_FILTERS: MonitorFilters = {
  target: "all",
  trigger: "all",
  status: "all",
  startAt: "",
  endAt: "",
};

export const REPORT_HISTORY_DAYS = 30;

const reportSteps = (activeIndex = -1, failedIndex = -1) => [
  { name: "会话探活", status: failedIndex === 0 ? "failed" : "completed", startedAt: "2026-07-17T09:00:00+08:00", finishedAt: "2026-07-17T09:00:18+08:00", message: failedIndex === 0 ? "登录状态失效，任务未完成" : "会话状态正常" },
  { name: "下载报表", status: activeIndex === 1 ? "active" : failedIndex === 1 ? "failed" : activeIndex > 1 ? "completed" : "pending", message: activeIndex === 1 ? "正在下载第 2 / 4 份报表" : "等待执行" },
  { name: "数据比对", status: activeIndex === 2 ? "active" : failedIndex === 2 ? "failed" : activeIndex > 2 ? "completed" : "pending", message: "等待执行" },
  { name: "更新模板", status: activeIndex === 3 ? "active" : failedIndex === 3 ? "failed" : activeIndex > 3 ? "completed" : "pending", message: "等待执行" },
  { name: "生成截图", status: activeIndex === 4 ? "active" : failedIndex === 4 ? "failed" : activeIndex > 4 ? "completed" : "pending", message: "等待执行" },
  { name: "发送企业微信", status: activeIndex === 5 ? "active" : failedIndex === 5 ? "failed" : activeIndex > 5 ? "completed" : "pending", message: "等待执行" },
] as MonitorRun["steps"];

export const monitorRuns: MonitorRun[] = [
  {
    id: "report-aijia-failed",
    targetId: "report-aijia",
    target: "通报 · 爱家亲情网每日盯控-网格-渠道经理",
    trigger: "scheduled",
    source: "prefect",
    status: "failed",
    scheduledAt: "2026-07-17T09:00:00+08:00",
    startedAt: "2026-07-17T09:02:18+08:00",
    finishedAt: "2026-07-17T09:03:05+08:00",
    currentStep: "会话探活",
    durationSeconds: 47,
    steps: reportSteps(-1, 0),
    logs: [
      { at: "2026-07-17T09:02:18+08:00", level: "INFO", message: "已创建通报运行，开始会话探活" },
      { at: "2026-07-17T09:03:05+08:00", level: "ERROR", message: "登录状态失效，任务未完成" },
    ],
    error: { category: "会话/认证", failedStep: "会话探活", businessSummary: "登录状态失效，任务未完成", technicalSummary: "认证状态校验失败，已隐藏会话细节。", retryCount: 0 },
  },
  {
    id: "report-pksai-running",
    targetId: "report-pksai",
    target: "通报 · 网格区公司 PK赛",
    trigger: "scheduled",
    source: "prefect",
    status: "running",
    scheduledAt: "2026-07-17T09:05:00+08:00",
    startedAt: "2026-07-17T09:05:12+08:00",
    currentStep: "下载报表 2/4",
    durationSeconds: 192,
    steps: reportSteps(1),
    logs: [
      { at: "2026-07-17T09:05:12+08:00", level: "INFO", message: "会话探活完成，开始下载报表" },
      { at: "2026-07-17T09:08:24+08:00", level: "INFO", message: "已完成 2 / 4 份报表下载" },
    ],
  },
  {
    id: "session-keeper-success",
    targetId: "session-keeper",
    target: "会话维护 · session-keeper",
    trigger: "session",
    source: "prefect",
    status: "succeeded",
    scheduledAt: "2026-07-17T08:50:00+08:00",
    startedAt: "2026-07-17T08:50:01+08:00",
    finishedAt: "2026-07-17T08:50:35+08:00",
    currentStep: "恢复确认",
    durationSeconds: 34,
    steps: [
      { name: "会话探活", status: "completed", message: "连接正常" },
      { name: "登录", status: "completed", message: "无需重新登录" },
      { name: "恢复确认", status: "completed", message: "会话可复用" },
    ],
    logs: [{ at: "2026-07-17T08:50:35+08:00", level: "INFO", message: "会话维护成功" }],
  },
  {
    id: "dashboard-realtime-success",
    targetId: "dashboard-realtime",
    target: "驾驶舱采集 · 实时指标",
    trigger: "scheduled",
    source: "prefect",
    status: "succeeded",
    scheduledAt: "2026-07-17T08:30:00+08:00",
    startedAt: "2026-07-17T08:30:00+08:00",
    finishedAt: "2026-07-17T08:32:08+08:00",
    currentStep: "采集并入库",
    durationSeconds: 128,
    steps: [
      { name: "指标采集", status: "completed", message: "采集完成" },
      { name: "指标计算", status: "completed", message: "计算完成" },
      { name: "入库", status: "completed", message: "已写入驾驶舱" },
    ],
    logs: [{ at: "2026-07-17T08:32:08+08:00", level: "INFO", message: "驾驶舱实时指标采集完成" }],
  },
  {
    id: "web-safety-success",
    targetId: "web-safety-test",
    target: "配置操作 · 安全测试",
    trigger: "web",
    source: "web",
    status: "succeeded",
    startedAt: "2026-07-17T08:20:12+08:00",
    finishedAt: "2026-07-17T08:20:21+08:00",
    currentStep: "校验完成",
    durationSeconds: 9,
    steps: [
      { name: "配置校验", status: "completed", message: "校验通过" },
      { name: "生成临时配置", status: "completed", message: "已完成" },
      { name: "执行 dry-run", status: "completed", message: "无真实发送" },
    ],
    logs: [{ at: "2026-07-17T08:20:21+08:00", level: "INFO", message: "安全测试完成" }],
  },
  {
    id: "report-pksai-success",
    targetId: "report-pksai",
    target: "通报 · 网格区公司 PK赛",
    trigger: "scheduled",
    source: "prefect",
    status: "succeeded",
    scheduledAt: "2026-07-16T21:40:00+08:00",
    startedAt: "2026-07-16T21:40:00+08:00",
    finishedAt: "2026-07-16T21:43:05+08:00",
    currentStep: "发送企业微信",
    durationSeconds: 185,
    steps: reportSteps(6),
    logs: [{ at: "2026-07-16T21:43:05+08:00", level: "INFO", message: "通报发送完成" }],
  },
  {
    id: "report-aijia-scheduled",
    targetId: "report-aijia",
    target: "通报 · 爱家亲情网每日盯控-网格-渠道经理",
    trigger: "scheduled",
    source: "prefect",
    status: "scheduled",
    scheduledAt: "2026-07-18T09:00:00+08:00",
    currentStep: "尚未开始",
    steps: reportSteps(),
    logs: [{ at: "2026-07-17T09:03:05+08:00", level: "WARN", message: "下一次定时运行将在计划时间开始" }],
  },
];

export const pendingQueue: PendingQueue = {
  scopeLabel: "数据库当前 Scheduled",
  total: 48,
  items: Array.from({ length: 48 }, (_, index) => {
    const targets = [
      { id: "session-keeper", name: "会话维护 · session-keeper", trigger: "session" as const, step: "会话探活" },
      { id: "dashboard-realtime", name: "驾驶舱采集 · 实时指标", trigger: "scheduled" as const, step: "指标采集" },
      { id: "report-aijia", name: "通报 · 爱家亲情网每日盯控-网格-渠道经理", trigger: "scheduled" as const, step: "会话探活" },
      { id: "report-pksai", name: "通报 · 网格区公司 PK赛", trigger: "scheduled" as const, step: "下载报表" },
    ];
    const target = targets[index % targets.length];
    const time = new Date(Date.UTC(2026, 6, 18, 9, 0) + index * 30 * 60 * 1000).toISOString().replace(".000Z", "+08:00");
    return { id: `scheduled-${index + 1}`, targetId: target.id, target: target.name, trigger: target.trigger, scheduledAt: time, nextStep: target.step };
  }),
};

function isRecentReportExecution(run: MonitorRun, now: Date): boolean {
  const reference = run.startedAt ?? run.scheduledAt;
  if (!reference || !run.target.startsWith("通报 · ") || run.status === "scheduled") return false;
  const timestamp = new Date(reference).getTime();
  const cutoff = now.getTime() - REPORT_HISTORY_DAYS * 24 * 60 * 60 * 1000;
  return Number.isFinite(timestamp) && timestamp >= cutoff && timestamp <= now.getTime();
}

export function filterRuns(runs: MonitorRun[], filters: MonitorFilters, now = new Date()): MonitorRun[] {
  return runs.filter((run) => {
    if (!isRecentReportExecution(run, now)) return false;
    if (filters.target !== "all" && run.targetId !== filters.target) return false;
    if (filters.trigger !== "all" && run.trigger !== filters.trigger) return false;
    if (filters.status !== "all" && run.status !== filters.status) return false;
    const referenceTime = run.startedAt ?? run.scheduledAt ?? "";
    if (filters.startAt && referenceTime < filters.startAt) return false;
    if (filters.endAt && referenceTime > filters.endAt) return false;
    return true;
  });
}

export function getSummary(runs: MonitorRun[]): MonitorSummary {
  const count = (status: RunStatus) => runs.filter((run) => run.status === status).length;
  return { succeeded: count("succeeded"), running: count("running"), failed: count("failed"), scheduled: count("scheduled") };
}

export async function getMonitorSnapshot(): Promise<MonitorSnapshot> {
  // Replace with GET /api/monitor/summary + /api/monitor/runs when the backend is connected.
  await new Promise((resolve) => window.setTimeout(resolve, 360));
  return { runs: structuredClone(monitorRuns), pendingQueue: structuredClone(pendingQueue), updatedAt: "2026-07-17T09:08:24+08:00", connected: true, upstream: { lastAcceptedAt: "2026-07-17T09:08:24+08:00", lastReconciledAt: "2026-07-17T09:08:24+08:00", lastErrorCategory: null, lastErrorAt: null, lastErrorDetail: null } };
}

export function createMockMonitorStream(onUpdate: (update: MonitorStreamMessage) => void) {
  // Replace with WebSocket /api/monitor/stream; keep this callback contract for live patches.
  let count = 2;
  const timer = window.setInterval(() => {
    count = count === 4 ? 1 : count + 1;
    const runs = structuredClone(monitorRuns);
    const active = runs.find((run) => run.id === "report-pksai-running");
    if (active) {
      active.currentStep = `下载报表 ${count}/4`;
      active.durationSeconds = 192 + count * 6;
      active.steps[1] = { ...active.steps[1], message: `正在下载第 ${count} / 4 份报表` };
      active.logs.push({ at: "2026-07-17T09:08:24+08:00", level: "INFO", message: `下载进度更新：${count} / 4` });
    }
    onUpdate({ type: "snapshot", runs, pendingQueue: structuredClone(pendingQueue), updatedAt: "2026-07-17T09:08:24+08:00", connected: true, upstream: { lastAcceptedAt: "2026-07-17T09:08:24+08:00", lastReconciledAt: "2026-07-17T09:08:24+08:00", lastErrorCategory: null, lastErrorAt: null, lastErrorDetail: null } });
  }, 4200);
  return () => window.clearInterval(timer);
}
