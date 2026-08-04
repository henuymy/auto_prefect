export type DashboardIndicator = {
  id: number;
  code: string;
  name: string;
  sort_order: number;
};

export type DashboardCatalogIndicator = DashboardIndicator & {
  enabled: boolean;
  source_active: boolean;
  indicator_type: "SOURCE" | "CUSTOM";
  storage_mode: "STORE" | "COMPONENT";
  removed_at: string | null;
};

export type DashboardIndicatorCatalogResponse = {
  indicators: DashboardCatalogIndicator[];
};

export type CustomIndicatorComponent = {
  source_code: string;
  source_name?: string;
  coefficient: number;
  source_enabled?: boolean;
  source_active?: boolean;
};

export type DashboardCustomIndicator = DashboardCatalogIndicator & {
  components: CustomIndicatorComponent[];
};

export type DashboardCustomIndicatorResponse = {
  indicators: DashboardCustomIndicator[];
};

export type SaveCustomIndicatorPayload = {
  code: string;
  name: string;
  enabled: boolean;
  sort_order?: number | null;
  components: Array<{
    source_code: string;
    coefficient?: number;
  }>;
};

export type UpdateIndicatorSettingsPayload = {
  enabled?: boolean;
  storage_mode?: "STORE" | "COMPONENT";
};

export type DashboardTargetScenario = "NORMAL" | "PK";
export type DashboardTargetPeriod = "DAY" | "MONTH";
export type DashboardTargetSource = "WORKING" | "ASSESSMENT";
export type DashboardTargetPlanStatus = "DRAFT" | "ACTIVE" | "RETIRED";

export type DashboardTargetPlan = {
  id: number;
  plan_name: string;
  scenario: DashboardTargetScenario;
  period_type: DashboardTargetPeriod;
  effective_from: string;
  effective_to: string | null;
  priority: number;
  version_no: number;
  status: DashboardTargetPlanStatus;
  is_realtime: boolean;
  supersedes_plan_id: number | null;
  activated_at: string | null;
  retired_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  value_count: number | null;
};

export type DashboardTargetPlanResponse = {
  plans: DashboardTargetPlan[];
};

export type CreateTargetPlanPayload = {
  plan_name: string;
  scenario: DashboardTargetScenario;
  period_type: DashboardTargetPeriod;
  effective_from: string;
  priority: number;
};

export type DashboardTargetValueRow = {
  node_id: number;
  node_type: DashboardRow["node_type"];
  node_code: string;
  node_name: string;
  indicator_id: number;
  indicator_code: string;
  indicator_name: string;
  target_value: number | null;
};

export type DashboardTargetValuesResponse = {
  plan: DashboardTargetPlan;
  rows: DashboardTargetValueRow[];
  row_count: number;
  node_count: number;
  indicator_count: number;
};

export type SaveTargetValuesPayload = {
  values: Array<{
    node_id: number;
    indicator_id: number;
    target_value: number | string;
  }>;
};

export type SaveTargetValuesResponse = {
  plan_id: number;
  saved: number;
  created: number;
  updated: number;
};

export type ImportTargetTemplateResponse = {
  plan: DashboardTargetPlan;
  imported: number;
  created: number;
  updated: number;
  skipped: Array<{
    sheet: string;
    row_number: number;
    node_type: string;
    node_code: string;
    indicator_code: string;
    reason: string;
  }>;
  skipped_count: number;
};

export type DashboardLatestRunResponse = {
  latest_run: {
    id: number;
    batch_no: string;
    stat_date: string | null;
    finished_at: string | null;
  } | null;
  data_version: string;
  config_version: string;
};

export type ChangeEntry = {
  value: number | null;
  rate: number | null;
};

export type DashboardValueMode = "REALTIME" | "REALTIME_ACC";

export type DashboardAccumulationMeta = {
  through_date: string;
  stat_date: string | null;
  is_fallback: boolean;
  baseline_zero: boolean;
  baseline_missing: boolean;
  target_period: "MONTH";
};

export type DashboardRow = {
  id: number;
  node_code: string;
  node_name: string;
  node_type: "CITY" | "BRANCH" | "GRID" | "CHANNEL_MANAGER" | "CHANNEL";
  level_no: number;
  parent_id: number | null;
  collection_run_id: number | null;
  collected_at: string | null;
  metrics: Record<string, number | null>;
  targets?: Record<string, number | null>;
};

/** Per-indicator change windows: changes[indicator_code]["change_5min"] = {value, rate} */
export type IndicatorChanges = Record<string, Record<string, ChangeEntry>>;

export type DashboardRowWithChanges = DashboardRow & {
  changes?: IndicatorChanges;
};

export type DashboardCurrentResponse = {
  data_mode?: DashboardValueMode;
  accumulation_meta?: DashboardAccumulationMeta;
  latest_run: {
    id: number;
    batch_no: string;
    stat_date: string | null;
    started_at: string | null;
    finished_at: string | null;
  } | null;
  indicators: DashboardIndicator[];
  rows: DashboardRow[];
  row_count: number;
};

export type DashboardChangesResponse = {
  data_mode?: DashboardValueMode | "HISTORY";
  accumulation_meta?: DashboardAccumulationMeta;
  selected_time?: string | null;
  data_version?: string;
  config_version?: string;
  coverage?: DashboardHistoryCoverage;
  history_meta?: DashboardHistoryMeta;
  latest_run: {
    id: number;
    batch_no: string;
    stat_date: string | null;
    started_at: string | null;
    finished_at: string | null;
  } | null;
  indicators: DashboardIndicator[];
  rows: DashboardRowWithChanges[];
  row_count: number;
};

export type DashboardHistoryRangeResponse = {
  earliest_at: string | null;
  latest_at: string | null;
};

export type DashboardHistoryOptionsResponse = {
  dates: Array<{ date: string; times: string[] }>;
  date_count: number;
};

export type DashboardAccOptionsResponse = {
  dates: string[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
};

export type DashboardHistoryCoverage = {
  levels: Partial<Record<"BRANCH" | "GRID" | "CHANNEL_MANAGER" | "CHANNEL", {
    expected_nodes: number;
    snapshot_nodes: number;
    missing_nodes: number;
    extra_nodes: number;
    available_metric_cells: number;
    total_metric_cells: number;
  }>>;
};

export type DashboardHistoryMeta = {
  batch_started_at: string | null;
  batch_finished_at: string | null;
  duration_seconds: number | null;
  fallback_seconds: number | null;
  is_fallback: boolean;
  time_basis: "BATCH_STARTED_AT";
  change_tolerance_minutes: number;
};

export type DashboardMatrixResponse = DashboardChangesResponse & {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export type DashboardAccResponse = {
  indicators: DashboardIndicator[];
  rows: DashboardRow[];
  row_count: number;
  through_date: string;
  stat_date: string | null;
  is_fallback: boolean;
  target_period: DashboardTargetPeriod;
  target_source: DashboardTargetSource;
  target_date: string | null;
};

export type DashboardOverviewResponse = {
  data_mode?: DashboardValueMode;
  accumulation_meta?: DashboardAccumulationMeta;
  latest_run: {
    id: number;
    batch_no: string;
    stat_date: string | null;
    finished_at: string | null;
  } | null;
  selected_branch: DashboardRow | null;
  indicators: DashboardIndicator[];
  rows: DashboardRowWithChanges[];
  acc_rows: DashboardRow[];
  row_count: number;
  acc_row_count: number;
};
