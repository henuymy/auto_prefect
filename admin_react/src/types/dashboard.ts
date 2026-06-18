export type DashboardIndicator = {
  id: number;
  code: string;
  name: string;
  sort_order: number;
};

export type ChangeEntry = {
  value: number | null;
  rate: number | null;
};

export type DashboardRow = {
  area_id: number;
  area_code: string;
  area_name: string;
  level_type: "CITY" | "BRANCH" | "GRID" | "CHANNEL";
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
  latest_run: {
    id: number;
    batch_no: string;
    stat_date: string | null;
    finished_at: string | null;
  } | null;
  indicators: DashboardIndicator[];
  rows: DashboardRow[];
  row_count: number;
};

export type DashboardChangesResponse = {
  latest_run: {
    id: number;
    batch_no: string;
    stat_date: string | null;
    finished_at: string | null;
  } | null;
  indicators: DashboardIndicator[];
  rows: DashboardRowWithChanges[];
  row_count: number;
};

export type DashboardAccResponse = {
  indicators: DashboardIndicator[];
  rows: DashboardRow[];
  row_count: number;
};

export type DashboardOverviewResponse = {
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

/** 趋势图数据点 */
export type TrendPoint = {
  collected_at: string;
  value: number | null;
};

/** 趋势图响应 */
export type DashboardTrendResponse = {
  area_id: number;
  indicator_code: string;
  points: TrendPoint[];
};
