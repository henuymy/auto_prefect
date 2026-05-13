import type { ConfigVersion, DownloadItem, ReportConfig, RunLog, RuntimeCleanupPreview, RuntimeEntry, SystemStatus, ValidationIssue } from "@/types/config";
import { uid } from "@/lib/utils";

const API_BASE = import.meta.env.VITE_API_BASE || "";

let logs: RunLog[] = [];

type RawDownloadItem = Partial<DownloadItem> & { csrf_headers_from_cookies?: unknown };
type RawReportConfig = ReportConfig & { download?: RawDownloadItem; downloads?: RawDownloadItem[]; env?: unknown; compare?: unknown };

function normalizeAuthPreset(value?: string) {
  if (value === "SSR Cookie") return "报表分析 Ssr-token";
  if (value === "地市作战 uapToken") return "地市平台 Uaptoken";
  if (value === "自定义高级") return "自定义";
  return value || "无";
}

export function normalizeReportConfig(config: RawReportConfig): ReportConfig {
  const { download: legacyDownload, env: _legacyEnv, compare: _legacyCompare, ...rest } = config;
  const downloads: RawDownloadItem[] = (rest.downloads?.length ? rest.downloads : legacyDownload ? [legacyDownload] : []) as RawDownloadItem[];

  return {
    ...rest,
    downloads: downloads.map((item, index) => {
      const { csrf_headers_from_cookies: _legacyCsrf, ...cleanItem } = item;
      const bodyType = cleanItem.body_type;
      return {
        ...cleanItem,
        name: cleanItem.name || `抓取项-${index + 1}`,
        stage: cleanItem.stage || "report_analysis",
        auth_preset: normalizeAuthPreset(cleanItem.auth_preset),
        headers: cleanItem.headers || {},
        data: bodyType === "raw" ? cleanItem.data : cleanItem.data || {},
      } as DownloadItem;
    }),
    compare_sources: config.compare_sources || [],
    send: {
      webhook_url: config.send?.webhook_url || "",
      workbook_name: config.send?.workbook_name || config.name || "",
      items: config.send?.items || [],
    },
    template_update: {
      update_condition: config.template_update?.update_condition || "any_changed",
      write_sheets: config.template_update?.write_sheets || "all_compared",
      send_when_same: config.template_update?.send_when_same ?? true,
    },
    wait_for_change: {
      enabled: config.wait_for_change?.enabled ?? false,
      poll_interval_seconds: config.wait_for_change?.poll_interval_seconds || 300,
      max_wait_minutes: config.wait_for_change?.max_wait_minutes || 180,
    },
    deployment: {
      enabled: config.deployment?.enabled ?? false,
      cron: config.deployment?.cron || "",
      timezone: config.deployment?.timezone || "Asia/Shanghai",
    },
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    try {
      const payload = JSON.parse(text);
      const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
      throw new Error(detail || `请求失败: ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(text || `请求失败: ${response.status}`);
      }
      throw error;
    }
  }
  return response.json() as Promise<T>;
}

function pushLog(status: RunLog["status"], title: string, message: string, details?: string) {
  const log: RunLog = {
    id: uid("log"),
    status,
    title,
    message,
    createdAt: new Date().toLocaleString("zh-CN", { hour12: false }),
    details,
  };
  logs = [log, ...logs].slice(0, 50);
  return log;
}

export async function listConfigs() {
  const configs = await request<ReportConfig[]>("/api/configs");
  return configs.map(normalizeReportConfig);
}

export async function getConfig(id: string) {
  return normalizeReportConfig(await request<ReportConfig>(`/api/configs/${encodeURIComponent(id)}`));
}

export async function createConfig(config: ReportConfig) {
  const created = normalizeReportConfig(await request<ReportConfig>("/api/configs", { method: "POST", body: JSON.stringify(normalizeReportConfig(config)) }));
  pushLog("success", "新建配置", `已创建 ${created.name}`);
  return created;
}

export async function updateConfig(id: string, config: ReportConfig) {
  const saved = normalizeReportConfig(await request<ReportConfig>(`/api/configs/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(normalizeReportConfig(config)) }));
  pushLog("success", "保存配置", `已写入 config/reports/${saved.name}.json`);
  return saved;
}

export async function saveDraftConfig(id: string, config: ReportConfig) {
  const saved = normalizeReportConfig(await request<ReportConfig>(`/api/configs/${encodeURIComponent(id)}/draft`, { method: "POST", body: JSON.stringify(normalizeReportConfig(config)) }));
  pushLog("success", "保存草稿", `已写入 runtime/drafts/${saved.name}.json`);
  return saved;
}

export async function deleteConfig(id: string) {
  const result = await request<{ ok: boolean; deleted?: string[] }>(`/api/configs/${encodeURIComponent(id)}`, { method: "DELETE" });
  const deleted = result.deleted?.join("\n");
  pushLog("success", "删除配置", `已删除配置 ${id}`, deleted);
  return result;
}

export async function validateConfig(config: ReportConfig): Promise<ValidationIssue[]> {
  const result = await request<{ issues: ValidationIssue[] }>(`/api/configs/${encodeURIComponent(config.id)}/validate`, {
    method: "POST",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  pushLog(result.issues.length ? "failed" : "success", "配置校验", result.issues.length ? `发现 ${result.issues.length} 个问题` : "配置校验通过");
  return result.issues;
}

export async function testRunConfig(config: ReportConfig) {
  const result = await request<{ flowRunId: string; status: string; message: string; output?: string; taskConfigPath?: string }>(`/api/configs/${encodeURIComponent(config.id)}/test-run`, {
    method: "POST",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  const log = pushLog("success", "测试运行", result.message, result.output || result.taskConfigPath);
  return { ...result, log };
}

export async function realTestRunConfig(config: ReportConfig) {
  const result = await request<{ flowRunId: string; status: string; message: string; output?: string; taskConfigPath?: string }>(`/api/configs/${encodeURIComponent(config.id)}/real-test-run`, {
    method: "POST",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  const log = pushLog("success", "真实试跑", result.message, result.output || result.taskConfigPath);
  return { ...result, log };
}

export async function publishConfig(config: ReportConfig) {
  const result = await request<{ deploymentId: string; status: string; message: string; output?: string; taskConfigPath?: string }>(`/api/configs/${encodeURIComponent(config.id)}/publish`, {
    method: "POST",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  const log = pushLog("success", "发布到调度", result.message, result.output || result.taskConfigPath);
  return { ...result, log };
}

export async function listRunLogs() {
  return structuredClone(logs);
}

export async function listRuntime(path = "") {
  return request<{ current: RuntimeEntry; items: RuntimeEntry[] }>(`/api/runtime?path=${encodeURIComponent(path)}`);
}

export async function deleteRuntime(path: string) {
  await request<{ ok: boolean }>(`/api/runtime?path=${encodeURIComponent(path)}`, { method: "DELETE" });
  pushLog("success", "清理 runtime", `已删除 runtime/${path}`);
}

export async function getSystemStatus() {
  return request<SystemStatus>("/api/status");
}

export async function listConfigVersions(configId: string) {
  return request<ConfigVersion[]>(`/api/configs/${encodeURIComponent(configId)}/versions`);
}

export async function restoreConfigVersion(configId: string, versionId: string) {
  const restored = normalizeReportConfig(await request<ReportConfig>(`/api/configs/${encodeURIComponent(configId)}/versions/${encodeURIComponent(versionId)}/restore`, {
    method: "POST",
  }));
  pushLog("success", "恢复配置版本", `已恢复 ${configId} -> ${versionId}`);
  return restored;
}

export async function previewRuntimeCleanup(days: number, path = "") {
  return request<RuntimeCleanupPreview>(`/api/runtime/cleanup-preview?days=${days}&path=${encodeURIComponent(path)}`);
}

export async function runRuntimeCleanup(days: number, path = "") {
  const result = await request<RuntimeCleanupPreview>(`/api/runtime/cleanup?days=${days}&path=${encodeURIComponent(path)}`, { method: "POST" });
  pushLog("success", "批量清理 runtime", `已删除 ${result.deleted?.length || 0} 个项目，释放 ${formatSize(result.totalSize)}`);
  return result;
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
