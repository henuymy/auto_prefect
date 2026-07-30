import type { ConfigVersion, DownloadItem, ReportConfig, RunLog, RuntimeCleanupPreview, RuntimeEntry, SystemStatus, ValidationIssue } from "@/types/config";
import type { CreateTargetPlanPayload, DashboardAccOptionsResponse, DashboardAccResponse, DashboardCatalogIndicator, DashboardChangesResponse, DashboardCurrentResponse, DashboardCustomIndicator, DashboardCustomIndicatorResponse, DashboardHistoryOptionsResponse, DashboardHistoryRangeResponse, DashboardIndicatorCatalogResponse, DashboardLatestRunResponse, DashboardMatrixResponse, DashboardOverviewResponse, DashboardTargetPlan, DashboardTargetPlanResponse, DashboardTargetScenario, DashboardTargetValuesResponse, DashboardValueMode, ImportTargetTemplateResponse, SaveCustomIndicatorPayload, SaveTargetValuesPayload, SaveTargetValuesResponse, UpdateIndicatorSettingsPayload } from "@/types/dashboard";
import { uid } from "@/lib/utils";

const API_BASE = import.meta.env.VITE_API_BASE || "";
const API_REQUEST_TIMEOUT_MS = 30_000;

let logs: RunLog[] = [];

type RawDownloadItem = Partial<DownloadItem> & { csrf_headers_from_cookies?: unknown };
type RawReportConfig = ReportConfig & { download?: RawDownloadItem; downloads?: RawDownloadItem[]; env?: unknown; compare?: unknown };
export type ConfigSource = NonNullable<ReportConfig["source"]>;

function normalizeAuthPreset(value?: string) {
  if (value === "SSR Cookie") return "报表分析 Ssr-token";
  if (value === "地市作战 uapToken") return "地市平台 Uaptoken";
  if (value === "自定义高级") return "自定义";
  return value || "无";
}

const AUTH_PRESET_STAGE: Record<string, string> = {
  "报表分析 Ssr-token": "report_analysis",
  "智慧运营 User-Info": "smart_ops",
  "地市平台 Uaptoken": "city_ops",
};

function normalizeDeploymentCrons(deployment?: Partial<ReportConfig["deployment"]>) {
  const crons = Array.isArray(deployment?.crons) ? deployment.crons : [];
  return crons.map((cron) => String(cron || "").trim()).filter(Boolean);
}

function sheetIdFromDocUrl(value?: string) {
  if (!value) return "";
  try {
    return new URL(value).searchParams.get("tab") || "";
  } catch {
    const match = value.match(/[?&]tab=([^&#]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
}

export function normalizeReportConfig(config: RawReportConfig): ReportConfig {
  const { download: legacyDownload, env: _legacyEnv, compare: _legacyCompare, ...rest } = config;
  const downloads: RawDownloadItem[] = (rest.downloads?.length ? rest.downloads : legacyDownload ? [legacyDownload] : []) as RawDownloadItem[];

  return {
    ...rest,
    downloads: downloads.map((item, index) => {
      const { csrf_headers_from_cookies: _legacyCsrf, ...cleanItem } = item;
      const bodyType = cleanItem.body_type;
      if (cleanItem.source === "tencent_sheet") {
        const parsedSheetId = sheetIdFromDocUrl(cleanItem.doc_url);
        const sheets = cleanItem.sheets?.length
          ? cleanItem.sheets
          : [{ sheet_name: "日报", sheet_id: "", range: "A1:Z1000", output_sheet_name: "日报" }];
        return {
          ...cleanItem,
          source: "tencent_sheet",
          name: cleanItem.name || `腾讯文档-${index + 1}`,
          headers: cleanItem.headers || {},
          file_id: cleanItem.file_id || "",
          doc_url: cleanItem.doc_url || "",
          output_filename: cleanItem.output_filename || "",
          sheets: sheets.map((sheet, sheetIndex) => (
            sheetIndex === 0 && !sheet.sheet_id && parsedSheetId ? { ...sheet, sheet_id: parsedSheetId } : sheet
          )),
        } as DownloadItem;
      }
      return {
        ...cleanItem,
        source: cleanItem.source || "http_api",
        name: cleanItem.name || `抓取项-${index + 1}`,
        stage: AUTH_PRESET_STAGE[normalizeAuthPreset(cleanItem.auth_preset)] || cleanItem.stage || "report_analysis",
        auth_preset: normalizeAuthPreset(cleanItem.auth_preset),
        headers: cleanItem.headers || {},
        data: bodyType === "raw" ? cleanItem.data : cleanItem.data || {},
      } as DownloadItem;
    }),
    compare_sources: (config.compare_sources || []).map((source) => ({
      ...source,
      engine: source.engine || "openpyxl",
      max_workers: source.max_workers || 4,
      sheet_mappings: source.sheet_mappings || [],
    })),
    send: {
      webhook_url: config.send?.webhook_url || "",
      workbook_name: config.send?.workbook_name || config.name || "",
      items: (config.send?.items || []).map((item) => (
        item.type === "image"
          ? { ...item, capture: item.capture || { mode: "used_range" }, text: undefined }
          : { ...item, text: item.text?.mode ? item.text : { mode: "used_range" }, capture: undefined }
      )),
    },
    template_update: {
      engine: config.template_update?.engine || "hybrid",
      update_condition: config.template_update?.update_condition || "any_changed",
      write_sheets: config.template_update?.write_sheets || "all_compared",
      send_when_same: config.template_update?.send_when_same ?? true,
    },
    wait_for_change: {
      enabled: config.wait_for_change?.enabled ?? false,
      poll_interval_seconds: config.wait_for_change?.poll_interval_seconds || 300,
      max_wait_minutes: config.wait_for_change?.max_wait_minutes || 180,
    },
    deployment: (() => {
      const crons = normalizeDeploymentCrons(config.deployment);
      return {
        enabled: crons.length > 0,
        crons,
        timezone: config.deployment?.timezone || "Asia/Shanghai",
      };
    })(),
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: AbortSignal.timeout(API_REQUEST_TIMEOUT_MS),
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new Error("请求超时，请检查后端服务");
    }
    throw error;
  }
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

export async function getCurrentDashboard(
  nodeType?: string,
  parentId?: number,
  indicatorCodes?: string[],
  valueMode: DashboardValueMode = "REALTIME",
  targetScenario: DashboardTargetScenario = "NORMAL",
) {
  const params = new URLSearchParams();
  if (nodeType) params.set("node_type", nodeType);
  if (parentId != null) params.set("parent_id", String(parentId));
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  params.set("value_mode", valueMode);
  params.set("target_scenario", targetScenario);
  const qs = params.toString();
  return request<DashboardCurrentResponse>(
    `/api/dashboard/current${qs ? `?${qs}` : ""}`,
  );
}

export async function getDashboardIndicators(
  includeArchived = false,
  enabledOnly = false,
) {
  const params = new URLSearchParams({
    include_archived: String(includeArchived),
    enabled_only: String(enabledOnly),
  });
  return request<DashboardIndicatorCatalogResponse>(
    `/api/dashboard/indicators?${params.toString()}`,
  );
}

export async function getDashboardCustomIndicators() {
  return request<DashboardCustomIndicatorResponse>("/api/dashboard/custom-indicators");
}

export async function saveDashboardCustomIndicator(payload: SaveCustomIndicatorPayload) {
  return request<DashboardCustomIndicator>("/api/dashboard/custom-indicators", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteDashboardCustomIndicator(code: string) {
  return request<{ code: string; deleted: boolean }>(
    `/api/dashboard/custom-indicators/${encodeURIComponent(code)}`,
    { method: "DELETE" },
  );
}

export async function updateDashboardIndicatorSettings(
  code: string,
  payload: UpdateIndicatorSettingsPayload,
) {
  return request<DashboardCatalogIndicator>(
    `/api/dashboard/indicators/${encodeURIComponent(code)}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}

export async function getDashboardTargetPlans(status?: string) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const query = params.toString();
  return request<DashboardTargetPlanResponse>(
    `/api/dashboard/target-plans${query ? `?${query}` : ""}`,
  );
}

export async function createDashboardTargetPlan(payload: CreateTargetPlanPayload) {
  return request<DashboardTargetPlan>("/api/dashboard/target-plans", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function activateDashboardTargetPlan(planId: number) {
  return request<DashboardTargetPlan>(
    `/api/dashboard/target-plans/${planId}/activate`,
    { method: "POST" },
  );
}

export async function setDashboardTargetPlanRealtime(planId: number) {
  return request<DashboardTargetPlan>(
    `/api/dashboard/target-plans/${planId}/use-for-realtime`,
    { method: "POST" },
  );
}

export async function cloneDashboardTargetPlan(planId: number) {
  return request<DashboardTargetPlan>(
    `/api/dashboard/target-plans/${planId}/clone`,
    { method: "POST" },
  );
}

export async function getDashboardTargetValues(params: {
  planId: number;
  nodeType?: string;
  indicatorCode?: string;
  search?: string;
  limit?: number;
}) {
  const query = new URLSearchParams({
    plan_id: String(params.planId),
    limit: String(params.limit ?? 500),
  });
  if (params.nodeType) query.set("node_type", params.nodeType);
  if (params.indicatorCode) query.set("indicator_code", params.indicatorCode);
  if (params.search) query.set("search", params.search);
  return request<DashboardTargetValuesResponse>(
    `/api/dashboard/target-values?${query.toString()}`,
  );
}

export async function saveDashboardTargetValues(
  planId: number,
  payload: SaveTargetValuesPayload,
) {
  return request<SaveTargetValuesResponse>(
    `/api/dashboard/target-plans/${planId}/values`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export function dashboardTargetTemplateUrl() {
  return `${API_BASE}/api/dashboard/target-template`;
}

export async function importDashboardTargetTemplate(planId: number, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(
    `${API_BASE}/api/dashboard/target-template/import?plan_id=${encodeURIComponent(String(planId))}`,
    {
      method: "POST",
      body: formData,
    },
  );
  if (!response.ok) {
    const text = await response.text();
    try {
      const payload = JSON.parse(text);
      const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
      throw new Error(detail || `导入失败: ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(text || `导入失败: ${response.status}`);
      }
      throw error;
    }
  }
  return response.json() as Promise<ImportTargetTemplateResponse>;
}

export async function getDashboardLatestRun() {
  return request<DashboardLatestRunResponse>("/api/dashboard/latest-run");
}

export async function getDashboardHistoryRange() {
  return request<DashboardHistoryRangeResponse>("/api/dashboard/history/range");
}

export async function getDashboardHistoryOptions(indicatorCodes?: string[]) {
  const params = new URLSearchParams();
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  const query = params.toString();
  return request<DashboardHistoryOptionsResponse>(
    `/api/dashboard/history/options${query ? `?${query}` : ""}`,
  );
}

export async function getDashboardAccOptions(page = 1, pageSize = 50) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return request<DashboardAccOptionsResponse>(
    `/api/dashboard/acc/options?${params.toString()}`,
  );
}

export async function getDashboardHistoryWithChanges(
  asOf: string,
  nodeType?: string,
  changeWindows?: number[],
  indicatorCodes?: string[],
  scopeMode: "default" | "all" = "default",
  parentId?: number,
  parentNodeType?: string,
  targetScenario: DashboardTargetScenario = "NORMAL",
) {
  const params = new URLSearchParams({ as_of: asOf, scope_mode: scopeMode });
  if (nodeType) params.set("node_type", nodeType);
  if (parentId != null) params.set("parent_id", String(parentId));
  if (parentNodeType) params.set("parent_node_type", parentNodeType);
  if (changeWindows?.length) params.set("change_windows", changeWindows.join(","));
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  params.set("target_scenario", targetScenario);
  return request<DashboardChangesResponse>(
    `/api/dashboard/history/current-with-changes?${params.toString()}`,
  );
}

export async function getDashboardHistoryMatrix(params: {
  asOf: string;
  nodeType: string;
  scopeMode: "default" | "all";
  parentId?: number;
  parentNodeType?: string;
  branchCode?: string;
  indicatorCodes: string[];
  changeWindow: number;
  search?: string;
  sortIndicator?: string;
  sortMode: string;
  page: number;
  pageSize: number;
  targetScenario?: DashboardTargetScenario;
}) {
  const query = new URLSearchParams({
    as_of: params.asOf,
    node_type: params.nodeType,
    scope_mode: params.scopeMode,
    branch_code: params.branchCode || "AQ",
    indicator_codes: params.indicatorCodes.join(","),
    change_window: String(params.changeWindow),
    sort_mode: params.sortMode,
    page: String(params.page),
    page_size: String(params.pageSize),
    target_scenario: params.targetScenario || "NORMAL",
  });
  if (params.parentId != null) query.set("parent_id", String(params.parentId));
  if (params.parentNodeType) query.set("parent_node_type", params.parentNodeType);
  if (params.search) query.set("search", params.search);
  if (params.sortIndicator) query.set("sort_indicator", params.sortIndicator);
  return request<DashboardMatrixResponse>(
    `/api/dashboard/history/matrix?${query.toString()}`,
  );
}

export async function getDashboardMatrix(params: {
  nodeType: string;
  scopeMode: "default" | "all";
  parentId?: number;
  parentNodeType?: string;
  branchCode?: string;
  indicatorCodes: string[];
  changeWindow: number;
  search?: string;
  sortIndicator?: string;
  sortMode: string;
  page: number;
  pageSize: number;
  valueMode?: DashboardValueMode;
  targetScenario?: DashboardTargetScenario;
}) {
  const query = new URLSearchParams({
    node_type: params.nodeType,
    scope_mode: params.scopeMode,
    branch_code: params.branchCode || "AQ",
    indicator_codes: params.indicatorCodes.join(","),
    change_window: String(params.changeWindow),
    sort_mode: params.sortMode,
    page: String(params.page),
    page_size: String(params.pageSize),
    value_mode: params.valueMode || "REALTIME",
    target_scenario: params.targetScenario || "NORMAL",
  });
  if (params.parentId != null) query.set("parent_id", String(params.parentId));
  if (params.parentNodeType) query.set("parent_node_type", params.parentNodeType);
  if (params.search) query.set("search", params.search);
  if (params.sortIndicator) query.set("sort_indicator", params.sortIndicator);
  return request<DashboardMatrixResponse>(
    `/api/dashboard/matrix?${query.toString()}`,
  );
}

export async function getDashboardOverview(
  branchId?: number,
  branchCode?: string,
  periodType: "DAY_ACC" | "MONTH" = "DAY_ACC",
  changeWindows?: number[],
  indicatorCodes?: string[],
  includeAcc = true,
  valueMode: DashboardValueMode = "REALTIME",
  targetScenario: DashboardTargetScenario = "NORMAL",
) {
  const params = new URLSearchParams();
  params.set("period_type", periodType);
  if (branchId != null) params.set("branch_id", String(branchId));
  if (branchCode) params.set("branch_code", branchCode);
  if (changeWindows?.length) params.set("change_windows", changeWindows.join(","));
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  params.set("include_acc", String(includeAcc));
  params.set("value_mode", valueMode);
  params.set("target_scenario", targetScenario);
  return request<DashboardOverviewResponse>(
    `/api/dashboard/overview?${params.toString()}`,
  );
}

export async function getDashboardWithChanges(
  nodeType?: string,
  parentId?: number,
  changeWindows?: number[],
  indicatorCodes?: string[],
  valueMode: DashboardValueMode = "REALTIME",
  targetScenario: DashboardTargetScenario = "NORMAL",
) {
  const params = new URLSearchParams();
  if (nodeType) params.set("node_type", nodeType);
  if (parentId != null) params.set("parent_id", String(parentId));
  if (changeWindows?.length) params.set("change_windows", changeWindows.join(","));
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  params.set("value_mode", valueMode);
  params.set("target_scenario", targetScenario);
  const qs = params.toString();
  return request<DashboardChangesResponse>(
    `/api/dashboard/current-with-changes${qs ? `?${qs}` : ""}`,
  );
}

export async function getAccDashboard(
  periodType: "DAY_ACC" | "MONTH" = "DAY_ACC",
  statDate?: string,
  nodeType?: string,
  parentId?: number,
  indicatorCodes?: string[],
  targetScenario: DashboardTargetScenario = "NORMAL",
) {
  const params = new URLSearchParams();
  params.set("period_type", periodType);
  if (statDate) params.set("stat_date", statDate);
  if (nodeType) params.set("node_type", nodeType);
  if (parentId != null) params.set("parent_id", String(parentId));
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  params.set("target_scenario", targetScenario);
  return request<DashboardAccResponse>(
    `/api/dashboard/acc?${params.toString()}`,
  );
}

export async function getDashboardDrillDown(
  parentId: number,
  parentNodeType: string,
  periodType: "DAY_ACC" | "MONTH" = "DAY_ACC",
  changeWindows?: number[],
  indicatorCodes?: string[],
  includeAcc = true,
  treeMode: "full" | "flat" = "full",
  valueMode: DashboardValueMode = "REALTIME",
  targetScenario: DashboardTargetScenario = "NORMAL",
) {
  const params = new URLSearchParams({
    parent_id: String(parentId),
    parent_node_type: parentNodeType,
    period_type: periodType,
    tree_mode: treeMode,
  });
  if (changeWindows?.length) params.set("change_windows", changeWindows.join(","));
  if (indicatorCodes?.length) params.set("indicator_codes", indicatorCodes.join(","));
  params.set("include_acc", String(includeAcc));
  params.set("value_mode", valueMode);
  params.set("target_scenario", targetScenario);
  return request<DashboardOverviewResponse>(
    `/api/dashboard/drill-down?${params.toString()}`,
  );
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

export async function updateConfigOrder(ids: string[]) {
  return request<{ ids: string[] }>("/api/configs/order", {
    method: "PUT",
    body: JSON.stringify({ ids }),
  });
}

export async function getConfig(id: string, source: ConfigSource = "published") {
  return normalizeReportConfig(await request<ReportConfig>(`/api/configs/${encodeURIComponent(id)}?source=${source}`));
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
  pushLog("success", "保存草稿", `已写入 C:\\AutoNotifyRuntime\\config\\drafts\\${saved.name}.json`);
  return saved;
}

export async function deleteConfig(id: string, source: ConfigSource = "published") {
  const result = await request<{ ok: boolean; deleted?: string[] }>(`/api/configs/${encodeURIComponent(id)}?source=${source}`, { method: "DELETE" });
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
  const result = await request<{
    deploymentId: string;
    status: string;
    message: string;
    output?: string;
    taskConfigPath?: string;
    crons?: string[];
    timezone?: string;
    scheduleStatus?: "enabled" | "disabled" | "none";
    publishMode?: "schedule-state-only";
  }>(`/api/configs/${encodeURIComponent(config.id)}/publish`, {
    method: "POST",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  const log = pushLog("success", "发布到调度", result.message, result.output || result.taskConfigPath);
  return { ...result, log };
}

export async function deleteDeployment(config: ReportConfig) {
  const result = await request<{
    deleted: boolean;
    deploymentId: string;
    deploymentName: string;
    message: string;
  }>(`/api/configs/${encodeURIComponent(config.id)}/deployment`, {
    method: "DELETE",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  const log = pushLog("success", "删除 Deployment", result.message, result.deploymentId);
  return { ...result, log };
}

export async function listRunLogs() {
  try {
    const backendLogs = await request<RunLog[]>("/api/run-logs");
    logs = backendLogs.length ? backendLogs : logs;
  } catch {
    // Keep the local in-memory fallback useful if the backend is not running.
  }
  return structuredClone(logs);
}

export async function uploadTemplate(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE}/api/templates/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const text = await response.text();
    try {
      const payload = JSON.parse(text);
      const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
      throw new Error(detail || `上传失败: ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(text || `上传失败: ${response.status}`);
      }
      throw error;
    }
  }
  const result = await response.json() as { filename: string; path: string; size: number };
  pushLog("success", "上传模板", `已上传 ${result.path}`);
  return result;
}

export async function listTemplates() {
  return request<Array<{ filename: string; path: string; size: number; modifiedAt: number }>>("/api/templates");
}

export async function deleteTemplate(filename: string) {
  const result = await request<{ ok: boolean; path: string }>(`/api/templates?filename=${encodeURIComponent(filename)}`, { method: "DELETE" });
  pushLog("success", "删除模板", `已删除 ${result.path}`);
  return result;
}

export function templateDownloadUrl(filename: string) {
  return `${API_BASE}/api/templates/download?filename=${encodeURIComponent(filename)}`;
}

export async function generateStarterTemplate(config: ReportConfig) {
  const result = await request<{
    status: "success";
    filename: string;
    template_path: string;
    required_stages: string[];
    merged_sheets: Array<{
      stage: string;
      download_name: string;
      source_sheet_name: string;
      template_sheet_name: string;
      source_path: string;
    }>;
  }>(`/api/configs/${encodeURIComponent(config.id)}/starter-template`, {
    method: "POST",
    body: JSON.stringify(normalizeReportConfig(config)),
  });
  pushLog("success", "生成新手模板", `已生成 ${result.template_path}`);
  return result;
}

export async function listRuntime(path = "") {
  return request<{ current: RuntimeEntry; items: RuntimeEntry[] }>(`/api/runtime?path=${encodeURIComponent(path)}`);
}

export async function deleteRuntime(path: string) {
  await request<{ ok: boolean }>(`/api/runtime?path=${encodeURIComponent(path)}`, { method: "DELETE" });
  pushLog("success", "清理 runtime", `已删除 ${path}`);
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
