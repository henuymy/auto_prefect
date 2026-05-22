export type RuntimeStatus = "success" | "failed" | "running" | "disabled";
export type ResponseMode = "file" | "json_to_excel" | "json_drilldown_to_excel";
export type BodyType = "form" | "json" | "raw";
export type ScheduleMode = "manual" | "daily" | "weekly" | "cron";

export interface ExcelColumn {
  field: string;
  header: string;
  type?: "number" | "";
}

export interface DownloadItem {
  name: string;
  enabled?: boolean;
  stage: string;
  auth_preset?: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  url: string;
  headers: Record<string, string>;
  body_type: BodyType;
  data?: Record<string, unknown>;
  raw_body?: string;
  response_mode: ResponseMode;
  headers_from_cookies?: Record<string, string>;
  headers_from_session_storage?: Record<string, string>;
  headers_from_local_storage?: Record<string, string>;
  headers_from_cookie_string?: Record<string, string>;
  excel?: {
    data_path?: string;
    sheet_name?: string;
    columns?: ExcelColumn[];
  };
  drilldown?: {
    data_path?: string;
    request_area_field?: string;
    next_area_field?: string;
    levels?: string[];
    max_requests?: number;
    max_workers?: number;
    skip_self_row?: boolean;
  };
}

export interface SheetMapping {
  name?: string;
  new_sheet_name: string;
  template_sheet_name: string;
  header_row?: number;
  ignore_columns?: string[];
  key_columns?: string[];
}

export interface CompareSource {
  download_name: string;
  engine: "openpyxl" | "com";
  max_workers: number;
  sheet_mappings: SheetMapping[];
}

export interface SendItem {
  type: "image" | "text";
  sheet: string;
  text?: {
    mode?: "used_range" | "none";
  };
}

export interface ReportConfig {
  id: string;
  name: string;
  template_path: string;
  enabled?: boolean;
  source?: "published" | "draft";
  has_draft?: boolean;
  description?: string;
  downloads: DownloadItem[];
  compare_sources: CompareSource[];
  send: {
    webhook_url: string;
    workbook_name: string;
    items: SendItem[];
  };
  template_update: {
    engine?: "hybrid" | "com_copy";
    update_condition: "any_changed" | "all_changed";
    write_sheets: "changed" | "all_compared";
    send_when_same: boolean;
  };
  wait_for_change: {
    enabled: boolean;
    poll_interval_seconds: number;
    max_wait_minutes: number;
  };
  deployment: {
    enabled: boolean;
    cron: string;
    timezone: string;
  };
  lastRun?: RuntimeStatus;
  updatedAt?: string;
}

export interface ValidationIssue {
  path: string;
  message: string;
}

export interface RunLog {
  id: string;
  status: RuntimeStatus;
  title: string;
  message: string;
  createdAt: string;
  details?: string;
}

export interface RuntimeEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number | null;
  modifiedAt: string;
}

export interface ConfigVersion {
  id: string;
  path: string;
  size: number;
  createdAt: string;
}

export interface RuntimeCleanupPreview {
  days: number;
  path: string;
  count: number;
  totalSize: number;
  items: RuntimeEntry[];
  deleted?: RuntimeEntry[];
}

export interface SystemStatus {
  backend: {
    ok: boolean;
    message: string;
    checked_at: string;
  };
  prefect: {
    ok: boolean;
    api_url: string;
    health_url: string;
    message: string;
  };
}
