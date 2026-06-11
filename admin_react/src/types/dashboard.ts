export type DashboardIndicator = {
  id: number;
  code: string;
  name: string;
  sort_order: number;
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
