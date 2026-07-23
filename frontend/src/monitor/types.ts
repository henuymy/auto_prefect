export type RunStatus = "scheduled" | "running" | "succeeded" | "failed" | "skipped";
export type RunSource = "prefect" | "web";
export type RunTrigger = "scheduled" | "session" | "manual" | "web";
export type LogLevel = "INFO" | "WARN" | "ERROR";

export interface MonitorStep {
  name: string;
  status: "completed" | "active" | "pending" | "failed";
  startedAt?: string;
  finishedAt?: string;
  message: string;
}

export interface MonitorLog {
  at: string;
  level: LogLevel;
  message: string;
}

export interface MonitorError {
  category: "会话/认证" | "业务" | "外部依赖" | "系统";
  failedStep: string;
  businessSummary: string;
  technicalSummary: string;
  retryCount: number;
}

export interface MonitorRun {
  id: string;
  targetId: string;
  target: string;
  trigger: RunTrigger;
  source: RunSource;
  status: RunStatus;
  scheduledAt?: string;
  startedAt?: string;
  finishedAt?: string;
  currentStep: string;
  durationSeconds?: number;
  steps: MonitorStep[];
  logs: MonitorLog[];
  error?: MonitorError;
  detailAvailable?: boolean;
  detailMessage?: string;
}

export interface MonitorFilters {
  target: string;
  trigger: "all" | RunTrigger;
  status: "all" | RunStatus;
  startAt: string;
  endAt: string;
}

export interface MonitorSummary {
  succeeded: number;
  running: number;
  failed: number;
  scheduled: number;
}

export interface MonitorUpstream {
  lastAcceptedAt: string | null;
  lastReconciledAt: string | null;
  lastErrorCategory: string | null;
  lastErrorAt: string | null;
  lastErrorDetail: string | null;
}

export interface PendingQueueItem {
  id: string;
  targetId: string;
  target: string;
  trigger: RunTrigger;
  scheduledAt: string;
  nextStep: string;
}

export interface PendingQueue {
  scopeLabel: string;
  total: number;
  items: PendingQueueItem[];
}

export interface MonitorSnapshot {
  runs: MonitorRun[];
  pendingQueue: PendingQueue;
  updatedAt: string;
  connected: boolean;
  upstream: MonitorUpstream;
}

export interface MonitorRunUpdatedMessage {
  type: "run.updated";
  runId: string;
  run: MonitorRun | null;
  pendingQueue: PendingQueue;
  summary: MonitorSummary;
  updatedAt: string;
  connected: boolean;
  upstream: MonitorUpstream;
}

export interface MonitorConnectionMessage {
  type: "connection";
  connected: boolean;
}

export interface MonitorUpstreamUpdatedMessage {
  type: "upstream.updated";
  updatedAt: string;
  upstream: MonitorUpstream;
}

export type MonitorStreamMessage =
  | ({ type: "snapshot" } & MonitorSnapshot)
  | MonitorRunUpdatedMessage
  | MonitorConnectionMessage
  | MonitorUpstreamUpdatedMessage;
