import { memo, startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart3,
  ChevronDown,
  Database,
  Plus,
  Search,
  RefreshCw,
  Settings,
  Signal,
  Trash2,
} from "lucide-react";
import {
  deleteDashboardCustomIndicator,
  getDashboardOverview,
  getDashboardDrillDown,
  getDashboardCustomIndicators,
  getDashboardIndicators,
  getDashboardLatestRun,
  getDashboardMatrix,
  getDashboardWithChanges,
  getCurrentDashboard,
  getAccDashboard,
  saveDashboardCustomIndicator,
  updateDashboardIndicatorSettings,
} from "@/lib/api";
import type {
  DashboardCustomIndicator,
  DashboardCatalogIndicator,
  DashboardRow,
  DashboardRowWithChanges,
  DashboardIndicator,
  IndicatorChanges,
} from "@/types/dashboard";

/* ── types ── */

type LevelKey = "BRANCH" | "GRID" | "CHANNEL";
type SortKey =
  | "progress"
  | "done"
  | `changeValue:${number}`
  | `changeRate:${number}`;

type Change = { value: number | null; rate: number | null };

type BoardRow = {
  areaId: number;
  parentId: number | null;
  name: string;
  done: number;
  target: number | null;
  changes: Record<number, Change>;
};

type LevelBoard = {
  key: LevelKey;
  index: string;
  title: string;
  total: number;
  dayRows: BoardRow[];
  monthRows: BoardRow[];
};

type DrillEntry = {
  areaId: number;
  areaName: string;
  levelType: string;
};

type LevelOverrideData = {
  changesRows: DashboardRowWithChanges[];
  accRows: DashboardRow[];
};

type ScopeMode = "default" | "all";
type CockpitMode = "single" | "multi";
type StorageMode = "STORE" | "COMPONENT";
type SourceMetricOption = {
  code: string;
  name: string;
};
type SourceFilterMode = "all" | "enabled" | "disabled" | "store" | "component";
const COEFFICIENT_PATTERN = /^-?\d+(\.\d{0,4})?$/;
const COEFFICIENT_INPUT_PATTERN = /^-?\d*(\.\d{0,4})?$/;
type MatrixSortMode =
  | "progressAsc" | "progressDesc"
  | "doneAsc" | "doneDesc"
  | "changeValueAsc" | "changeValueDesc"
  | "changeRateAsc" | "changeRateDesc";

type LevelAllPopup = {
  kind: "loading" | "error";
  message: string;
} | null;

type ScopePopupTarget = {
  label: string;
};

const detailsCloseTimers = new WeakMap<HTMLDetailsElement, number>();

function keepDetailsOpen(event: React.MouseEvent<HTMLDetailsElement>) {
  const timer = detailsCloseTimers.get(event.currentTarget);
  if (timer != null) {
    window.clearTimeout(timer);
    detailsCloseTimers.delete(event.currentTarget);
  }
}

function closeDetailsAfterLeave(event: React.MouseEvent<HTMLDetailsElement>) {
  const details = event.currentTarget;
  const previousTimer = detailsCloseTimers.get(details);
  if (previousTimer != null) window.clearTimeout(previousTimer);
  const timer = window.setTimeout(() => {
    details.removeAttribute("open");
    detailsCloseTimers.delete(details);
  }, 250);
  detailsCloseTimers.set(details, timer);
}

/** Raw fetched data that doesn't change when indicator selection changes. */
type FetchedData = {
  online: boolean;
  latestRun: {
    batch_no: string;
    finished_at: string | null;
  } | null;
  updatedAt: string;
  indicators: DashboardIndicator[];
  indicatorCatalog: DashboardCatalogIndicator[];
  changesRows: DashboardRowWithChanges[];
  accRows: DashboardRow[];
  levelOverrides: Partial<Record<LevelKey, LevelOverrideData>>;
};

type DashboardCacheEntry = {
  data: FetchedData;
  cachedAt: number;
};

function makeDashboardCacheKey({
  mode,
  scopeMode,
  parentId,
  parentLevel,
  changeWindows,
  indicatorCodes,
  dayLevelAllMode,
  monthLevelAllMode,
}: {
  mode: CockpitMode;
  scopeMode: ScopeMode;
  parentId?: number | null;
  parentLevel?: string | null;
  changeWindows: number[];
  indicatorCodes?: string[] | null;
  dayLevelAllMode: Partial<Record<LevelKey, boolean>>;
  monthLevelAllMode: Partial<Record<LevelKey, boolean>>;
}) {
  return JSON.stringify({
    mode,
    scopeMode,
    parentId: parentId ?? null,
    parentLevel: parentLevel ?? null,
    changeWindows,
    indicatorCodes: indicatorCodes ?? null,
    dayLevelAll: {
      grid: Boolean(dayLevelAllMode.GRID),
      channel: Boolean(dayLevelAllMode.CHANNEL),
    },
    monthLevelAll: {
      grid: Boolean(monthLevelAllMode.GRID),
      channel: Boolean(monthLevelAllMode.CHANNEL),
    },
  });
}

/* ── constants ── */

const DEFAULT_CHANGE_WINDOWS = [5, 15, 30, 60];
const SHOW_MONTH_ACCUMULATION = false;
const DASHBOARD_CACHE_TTL_MS = 2 * 60 * 1000;
const DASHBOARD_CACHE_MAX_ENTRIES = 24;
const MATRIX_ROW_HEIGHT = 58;
const MATRIX_VIRTUAL_OVERSCAN = 8;
const SINGLE_ROW_HEIGHT = 38;
const SINGLE_VIRTUAL_OVERSCAN = 10;

const LEVEL_CONFIG: {
  key: LevelKey;
  index: string;
  title: string;
}[] = [
  { key: "BRANCH", index: "01", title: "分公司级" },
  { key: "GRID", index: "02", title: "网格级" },
  { key: "CHANNEL", index: "03", title: "渠道级" },
];

const DEFAULT_BRANCH_KEYWORDS = ["中原", "AQ"];

function visibleLevels(drill: DrillEntry | null): LevelKey[] {
  if (!drill) return ["BRANCH", "GRID", "CHANNEL"];
  if (drill.levelType === "CITY") return ["BRANCH", "GRID", "CHANNEL"];
  if (drill.levelType === "BRANCH") return ["BRANCH", "GRID", "CHANNEL"];
  if (drill.levelType === "GRID") return ["BRANCH", "GRID", "CHANNEL"];
  return [];
}

function normalizeDrillStack(items: DrillEntry[]): DrillEntry[] {
  let lastBranchIndex = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].levelType === "BRANCH") {
      lastBranchIndex = index;
      break;
    }
  }
  if (lastBranchIndex < 0) {
    const grid = items.find((item) => item.levelType === "GRID");
    return grid ? [grid] : [];
  }

  const branch = items[lastBranchIndex];
  const grid = items
    .slice(lastBranchIndex + 1)
    .find((item) => item.levelType === "GRID");
  return grid ? [branch, grid] : [branch];
}

function sameDrillStack(left: DrillEntry[], right: DrillEntry[]) {
  return (
    left.length === right.length &&
    left.every((item, index) => (
      item.areaId === right[index].areaId &&
      item.levelType === right[index].levelType
    ))
  );
}

/* ── helpers ── */

function nowText() {
  return new Date().toLocaleString("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatNumber(value: number) {
  return value.toLocaleString("zh-CN");
}

function fmtPct(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function fmtProgress(value: number | null) {
  return value == null ? "--" : fmtPct(value);
}

function nextFiveMinuteTimeText(source?: string | null) {
  const d = source ? new Date(source) : new Date();
  if (Number.isNaN(d.getTime())) return "--";
  const next = Math.ceil((d.getMinutes() + 1) / 5) * 5;
  if (next >= 60) {
    d.setHours(d.getHours() + 1);
    d.setMinutes(0);
  } else {
    d.setMinutes(next);
  }
  d.setSeconds(0);
  d.setMilliseconds(0);
  return d.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

function signed(value: number) {
  return value > 0 ? `+${value}` : `${value}`;
}

function signedPct(value: number) {
  if (value > 0) return `+${value.toFixed(1)}%`;
  if (value < 0) return `${value.toFixed(1)}%`;
  return "0%";
}

function normalizeChangeWindowMinutes(value: number, fallback = 5) {
  if (!Number.isFinite(value)) return fallback;
  const rounded = Math.round(value / 5) * 5;
  return Math.max(5, Math.min(1440, rounded));
}

function sortRows(
  rows: BoardRow[],
  sortKey: SortKey,
  direction: "asc" | "desc" = "desc",
) {
  return [...rows].sort((left, right) => {
    let score = 0;
    if (sortKey === "progress") {
      score = progressScore(left) - progressScore(right);
    } else if (sortKey.startsWith("changeRate:")) {
      const m = Number(sortKey.replace("changeRate:", ""));
      score = (left.changes[m].rate ?? -Infinity) - (right.changes[m].rate ?? -Infinity);
    } else if (sortKey.startsWith("changeValue:")) {
      const m = Number(sortKey.replace("changeValue:", ""));
      score = (left.changes[m].value ?? -Infinity) - (right.changes[m].value ?? -Infinity);
    } else {
      score = left.done - right.done;
    }
    return direction === "asc" ? score : -score;
  });
}

function pct(row: BoardRow) {
  return row.target != null && row.target > 0 ? row.done / row.target : null;
}

function progressScore(row: BoardRow) {
  return pct(row) ?? -1;
}

/* ── data → board transform (pure, no side‑effects) ── */

function buildBoards(
  changesRows: DashboardRowWithChanges[],
  accRows: DashboardRow[],
  activeCode: string,
  visible: LevelKey[],
  defaultScopedLowerLevels: boolean,
  dayUnscopedLevels: LevelKey[],
  monthUnscopedLevels: LevelKey[],
  changeWindows: number[],
): { dayLevels: LevelBoard[]; monthLevels: LevelBoard[] } {
  const defaultScope = defaultScopedLowerLevels
    ? buildDefaultBranchScope(changesRows)
    : null;
  const scopedChangesRows = defaultScopedLowerLevels
    ? scopeLowerLevelsToDefaultBranch(changesRows, defaultScope, dayUnscopedLevels)
    : changesRows;
  const scopedAccRows = defaultScopedLowerLevels
    ? scopeLowerLevelsToDefaultBranch(accRows, defaultScope, monthUnscopedLevels)
    : accRows;

  const accBy = new Map<number, number>();
  for (const r of scopedAccRows) {
    const v = r.metrics[activeCode];
    if (v != null) accBy.set(r.area_id, v);
  }

  const dayLevels = _buildLevels(
    scopedChangesRows,
    activeCode,
    visible,
    (r, _max) => {
      const done = r.metrics[activeCode] ?? 0;
      const cs = (r.changes || {})[activeCode] || {};
      return {
        areaId: r.area_id,
        parentId: r.parent_id,
        name: r.area_name,
        done,
        target: r.targets?.[activeCode] ?? null,
        changes: changesForWindows(cs, changeWindows),
      };
    },
  );

  const monthLevels = scopedAccRows.length
    ? _buildLevels(scopedAccRows, activeCode, visible, (r, _max) => ({
        areaId: r.area_id,
        parentId: r.parent_id,
        name: r.area_name,
        done: r.metrics[activeCode] ?? 0,
        target: r.targets?.[activeCode] ?? null,
        changes: emptyChanges(changeWindows),
      }))
    : [];

  return { dayLevels, monthLevels };
}

function changesForWindows(
  source: Record<string, Change>,
  changeWindows: number[],
): Record<number, Change> {
  return Object.fromEntries(
    changeWindows.map((minutes) => [
      minutes,
      source[`change_${minutes}min`] ?? { value: null, rate: null },
    ]),
  );
}

function emptyChanges(changeWindows: number[]): Record<number, Change> {
  return Object.fromEntries(
    changeWindows.map((minutes) => [minutes, { value: null, rate: null }]),
  );
}

function sortOptions(changeWindows: number[]): { key: SortKey; label: string }[] {
  return [
    { key: "progress", label: "完成进度" },
    { key: "done", label: "完成量" },
    ...changeWindows.flatMap((minutes) => [
      { key: `changeValue:${minutes}` as SortKey, label: `${minutes}分钟变化量` },
      { key: `changeRate:${minutes}` as SortKey, label: `${minutes}分钟变化率` },
    ]),
  ];
}

function normalizeSorts(
  sorts: Record<LevelKey, SortKey>,
  changeWindows: number[],
): Record<LevelKey, SortKey> {
  const valid = new Set(sortOptions(changeWindows).map((option) => option.key));
  return Object.fromEntries(
    Object.entries(sorts).map(([level, sortKey]) => [
      level,
      valid.has(sortKey) ? sortKey : "done",
    ]),
  ) as Record<LevelKey, SortKey>;
}

function applyLevelOverrides<T extends DashboardRow>(
  rows: T[],
  overrides: Partial<Record<LevelKey, LevelOverrideData>>,
  field: "changesRows" | "accRows",
): T[] {
  let result = rows;
  for (const level of ["GRID", "CHANNEL"] as LevelKey[]) {
    const override = overrides[level]?.[field] as T[] | undefined;
    if (override) {
      result = [
        ...result.filter((row) => row.level_type !== level),
        ...override,
      ];
    }
  }
  return result;
}

function buildDefaultBranchScope(rows: DashboardRow[]): {
  branchId: number;
  gridIds: Set<number>;
} | null {
  const defaultBranch = rows.find(
    (row) =>
      row.level_type === "BRANCH" &&
      DEFAULT_BRANCH_KEYWORDS.some(
        (keyword) =>
          row.area_name.includes(keyword) || row.area_code.includes(keyword),
      ),
  );
  if (!defaultBranch) return null;

  const gridIds = new Set(
    rows
      .filter(
        (row) =>
          row.level_type === "GRID" && row.parent_id === defaultBranch.area_id,
      )
      .map((row) => row.area_id),
  );

  return { branchId: defaultBranch.area_id, gridIds };
}

function scopeLowerLevelsToDefaultBranch<T extends DashboardRow>(
  rows: T[],
  scope: { branchId: number; gridIds: Set<number> } | null,
  unscopedLevels: LevelKey[] = [],
): T[] {
  if (!scope) return rows;
  const unscoped = new Set(unscopedLevels);

  return rows.filter((row) => {
    if (unscoped.has(row.level_type as LevelKey)) return true;
    if (row.level_type === "BRANCH") return true;
    if (row.level_type === "GRID") return row.parent_id === scope.branchId;
    if (row.level_type === "CHANNEL") {
      return row.parent_id != null && scope.gridIds.has(row.parent_id);
    }
    return true;
  });
}

function _buildLevels<T extends DashboardRow>(
  rows: T[],
  code: string,
  visible: LevelKey[],
  toRow: (r: T, maxDone: number) => BoardRow,
): LevelBoard[] {
  const byLevel = new Map<LevelKey, T[]>();
  for (const r of rows) {
    const k = r.level_type as LevelKey;
    if (!visible.includes(k)) continue;
    const list = byLevel.get(k) || [];
    list.push(r);
    byLevel.set(k, list);
  }
  return LEVEL_CONFIG.map((cfg) => {
    const items = byLevel.get(cfg.key) || [];
    const max = items.reduce((mx, r) => Math.max(mx, r.metrics[code] ?? 0), 0);
    return {
      key: cfg.key,
      index: cfg.index,
      title: cfg.title,
      total: items.length,
      dayRows: items.map((r) => toRow(r, max)),
      monthRows: [],
    };
  });
}

/* ── main component ── */

export function DashboardCockpit() {
  /* state */
  const branchPanelRef = useRef<HTMLDivElement>(null);
  const monthBranchPanelRef = useRef<HTMLDivElement>(null);
  const [branchHeight, setBranchHeight] = useState<number>(0);
  const [monthBranchHeight, setMonthBranchHeight] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<CockpitMode>("single");
  const [data, setData] = useState<FetchedData | null>(null);
  const [error, setError] = useState(false);
  const [indicator, setIndicator] = useState("");
  const [changeWindows, setChangeWindows] = useState<number[]>(DEFAULT_CHANGE_WINDOWS);
  const [multiMetricWindow, setMultiMetricWindow] = useState(60);
  const [multiSelectedCodes, setMultiSelectedCodes] = useState<string[]>([]);
  const [dataRevision, setDataRevision] = useState(0);
  const [drillStack, setDrillStack] = useState<DrillEntry[]>([]);
  const [scopeMode, setScopeMode] = useState<ScopeMode>("default");
  const [dayLevelAllMode, setDayLevelAllMode] = useState<Partial<Record<LevelKey, boolean>>>({});
  const [monthLevelAllMode, setMonthLevelAllMode] = useState<Partial<Record<LevelKey, boolean>>>({});
  const [pendingLevelAll, setPendingLevelAll] = useState<{
    section: "day" | "month";
    level: LevelKey;
    label: string;
  } | null>(null);
  const [backgroundLoadingLevels, setBackgroundLoadingLevels] = useState<
    Partial<Record<LevelKey, boolean>>
  >({});
  const [levelAllPopup, setLevelAllPopup] = useState<LevelAllPopup>(null);
  const [customManagerOpen, setCustomManagerOpen] = useState(false);
  const scopePopupTargetRef = useRef<ScopePopupTarget | null>(null);
  const fetchSeqRef = useRef(0);
  const queryCacheRef = useRef<Map<string, DashboardCacheEntry>>(new Map());
  const latestDataVersionRef = useRef<string | null>(null);
  const [daySorts, setDaySorts] = useState<Record<LevelKey, SortKey>>({
    BRANCH: "done",
    GRID: "done",
    CHANNEL: "done",
  });
  const [monthSorts, setMonthSorts] = useState<Record<LevelKey, SortKey>>({
    BRANCH: "done",
    GRID: "done",
    CHANNEL: "done",
  });

  const normalizedDrillStack = useMemo(
    () => normalizeDrillStack(drillStack),
    [drillStack],
  );
  const drillTarget = normalizedDrillStack.length > 0
    ? normalizedDrillStack[normalizedDrillStack.length - 1]
    : null;
  const visible = visibleLevels(drillTarget);
  const orgScopeName = normalizedDrillStack.length
    ? `郑州市 / ${normalizedDrillStack.map((item) => item.areaName).join(" / ")}`
    : scopeMode === "all"
      ? "郑州市 / 全部"
      : "郑州市 / 中原区";
  const requestedChangeWindows = useMemo(
    () => mode === "multi" ? [multiMetricWindow] : changeWindows,
    [changeWindows, mode, multiMetricWindow],
  );
  const parentChangeWindows = useMemo(
    () => mode === "multi" ? [] : requestedChangeWindows,
    [mode, requestedChangeWindows],
  );
  const requestedIndicatorCodes = useMemo(
    () => mode === "multi"
      ? multiSelectedCodes
      : indicator
        ? [indicator]
        : undefined,
    [indicator, mode, multiSelectedCodes],
  );
  const queryCacheKey = makeDashboardCacheKey({
    mode,
    scopeMode,
    parentId: drillTarget?.areaId ?? null,
    parentLevel: drillTarget?.levelType ?? null,
    changeWindows: parentChangeWindows,
    indicatorCodes: requestedIndicatorCodes ?? null,
    dayLevelAllMode,
    monthLevelAllMode,
  });
  const selectableIndicators = useMemo(
    () => (data?.indicatorCatalog || [])
      .filter((item) => item.enabled),
    [data?.indicatorCatalog],
  );
  const handleModeChange = useCallback((nextMode: CockpitMode) => {
    if (nextMode === mode) return;

    const nextChangeWindows = nextMode === "multi" ? [] : changeWindows;
    const nextIndicatorCodes = nextMode === "multi"
      ? multiSelectedCodes
      : indicator
        ? [indicator]
        : undefined;
    const nextCacheKey = makeDashboardCacheKey({
      mode: nextMode,
      scopeMode,
      parentId: drillTarget?.areaId ?? null,
      parentLevel: drillTarget?.levelType ?? null,
      changeWindows: nextChangeWindows,
      indicatorCodes: nextIndicatorCodes ?? null,
      dayLevelAllMode,
      monthLevelAllMode,
    });
    const cached = queryCacheRef.current.get(nextCacheKey);
    const cacheFresh = cached && Date.now() - cached.cachedAt <= DASHBOARD_CACHE_TTL_MS;

    if (cacheFresh) {
      setData(cached.data);
      setLoading(false);
      setError(false);
      setPendingLevelAll(null);
      setBackgroundLoadingLevels({});
      scopePopupTargetRef.current = null;
      setLevelAllPopup((prev) => (prev?.kind === "loading" ? null : prev));
    } else {
      setLoading(true);
      setError(false);
      setBackgroundLoadingLevels(
        nextMode === "single" && scopeMode === "all" && !drillTarget
          ? { GRID: true, CHANNEL: true }
          : {},
      );
    }

    setMode(nextMode);
  }, [
    changeWindows,
    dayLevelAllMode,
    drillTarget,
    indicator,
    mode,
    monthLevelAllMode,
    multiSelectedCodes,
    scopeMode,
  ]);

  useEffect(() => {
    if (!sameDrillStack(drillStack, normalizedDrillStack)) {
      setDrillStack(normalizedDrillStack);
    }
  }, [drillStack, normalizedDrillStack]);

  /* Query cache is reused for navigation/filter state; timed refresh bypasses it. */
  const fetchData = useCallback(async (forceRefresh = false) => {
    const seq = fetchSeqRef.current + 1;
    fetchSeqRef.current = seq;
    const cached = queryCacheRef.current.get(queryCacheKey);
    if (
      !forceRefresh &&
      cached &&
      Date.now() - cached.cachedAt <= DASHBOARD_CACHE_TTL_MS
    ) {
      setData(cached.data);
      setError(false);
      setLoading(false);
      setPendingLevelAll(null);
      scopePopupTargetRef.current = null;
      setLevelAllPopup((prev) => (prev?.kind === "loading" ? null : prev));
      return;
    }
    if (cached) {
      queryCacheRef.current.delete(queryCacheKey);
    }
    setLoading(true);
    setError(false);
    let levelAllOverrideFailed = false;
    let requestFailed = false;
    try {
      const parentId = drillTarget?.areaId;
      const parentLevel = drillTarget?.levelType;
      const catalogPromise = getDashboardIndicators(false, true)
        .catch(() => ({ indicators: [] }));
      const [changesData, accRows] = mode === "multi"
        ? await getCurrentDashboard(
            "BRANCH",
            undefined,
            requestedIndicatorCodes,
          ).then((changes) => [changes, [] as DashboardRow[]] as const)
        : parentId == null
        ? scopeMode === "all"
          ? SHOW_MONTH_ACCUMULATION
            ? await Promise.all([
                getDashboardWithChanges(
                  "BRANCH",
                  undefined,
                  requestedChangeWindows,
                  requestedIndicatorCodes,
                ),
                getAccDashboard(
                  "DAY_ACC",
                  undefined,
                  "BRANCH",
                  undefined,
                  requestedIndicatorCodes,
                ),
              ]).then(([changes, acc]) => [changes, acc.rows] as const)
            : await getDashboardWithChanges(
                "BRANCH",
                undefined,
                requestedChangeWindows,
                requestedIndicatorCodes,
              ).then((changes) => [changes, [] as DashboardRow[]] as const)
          : await getDashboardOverview(
              undefined,
              "AQ",
              "DAY_ACC",
              requestedChangeWindows,
              requestedIndicatorCodes,
              SHOW_MONTH_ACCUMULATION,
            ).then((overview) => [
              overview,
              overview.acc_rows,
            ] as const)
        : parentLevel
          ? await getDashboardDrillDown(
              parentId,
              parentLevel,
              "DAY_ACC",
              requestedChangeWindows,
              requestedIndicatorCodes,
              SHOW_MONTH_ACCUMULATION,
            ).then((drill) => [
              drill,
              drill.acc_rows,
            ] as const)
        : SHOW_MONTH_ACCUMULATION
          ? await Promise.all([
              getDashboardWithChanges(
                undefined,
                parentId,
                requestedChangeWindows,
                requestedIndicatorCodes,
              ),
              getAccDashboard(
                "DAY_ACC",
                undefined,
                undefined,
                parentId,
                requestedIndicatorCodes,
              ),
            ]).then(([changes, acc]) => [changes, acc.rows] as const)
          : await getDashboardWithChanges(
              undefined,
              parentId,
              requestedChangeWindows,
              requestedIndicatorCodes,
            )
              .then((changes) => [changes, [] as DashboardRow[]] as const);

      const progressiveAllLevels = (
        mode === "single"
        && parentId == null
        && scopeMode === "all"
      );
      const catalog = await catalogPromise;
      const latestRun = changesData.latest_run ?? null;
      const levelOverrides: Partial<Record<LevelKey, LevelOverrideData>> = {};
      const makeData = (): FetchedData => ({
        online: Boolean(latestRun),
        latestRun,
        updatedAt: latestRun?.finished_at
          ? new Date(latestRun.finished_at).toLocaleString("zh-CN", { hour12: false })
          : nowText(),
        indicators: changesData.indicators,
        indicatorCatalog: catalog.indicators,
        changesRows: changesData.rows,
        accRows,
        levelOverrides: { ...levelOverrides },
      });
      const cacheData = (nextData: FetchedData) => {
        queryCacheRef.current.set(queryCacheKey, {
          data: nextData,
          cachedAt: Date.now(),
        });
        while (queryCacheRef.current.size > DASHBOARD_CACHE_MAX_ENTRIES) {
          const oldestKey = queryCacheRef.current.keys().next().value;
          if (oldestKey == null) break;
          queryCacheRef.current.delete(oldestKey);
        }
      };

      if (progressiveAllLevels && seq === fetchSeqRef.current) {
        const branchData = makeData();
        setBackgroundLoadingLevels({ GRID: true, CHANNEL: true });
        startTransition(() => {
          setData(branchData);
          setDataRevision((current) => current + 1);
        });
      } else {
        setBackgroundLoadingLevels({});
      }

      const overrideLevels = (["GRID", "CHANNEL"] as LevelKey[])
        .filter((level) => (
          progressiveAllLevels
          || dayLevelAllMode[level]
          || monthLevelAllMode[level]
        ));
      await Promise.all(
        overrideLevels
          .map(async (level) => {
            try {
              const overrideChanges = await getDashboardWithChanges(
                  level,
                  undefined,
                  requestedChangeWindows,
                  requestedIndicatorCodes,
                );
              const overrideAccRows = SHOW_MONTH_ACCUMULATION
                ? await getAccDashboard(
                    "DAY_ACC",
                    undefined,
                    level,
                    undefined,
                    requestedIndicatorCodes,
                  )
                    .then((accData) => accData.rows)
                    .catch(() => [])
                : [];
              levelOverrides[level] = {
                changesRows: overrideChanges.rows,
                accRows: overrideAccRows,
              };
              if (progressiveAllLevels && seq === fetchSeqRef.current) {
                const progressiveData = makeData();
                startTransition(() => {
                  setData(progressiveData);
                  setDataRevision((current) => current + 1);
                });
                setBackgroundLoadingLevels((previous) => ({
                  ...previous,
                  [level]: false,
                }));
              }
            } catch {
              levelAllOverrideFailed = true;
              if (seq === fetchSeqRef.current) {
                const failedLabel = level === "GRID" ? "全部网格" : "全部渠道";
                setLevelAllPopup({
                  kind: "error",
                  message: `${failedLabel}请求失败，请重试`,
                });
                if (!progressiveAllLevels) {
                  setDayLevelAllMode((prev) => ({ ...prev, [level]: false }));
                  setMonthLevelAllMode((prev) => ({ ...prev, [level]: false }));
                }
                setBackgroundLoadingLevels((previous) => ({
                  ...previous,
                  [level]: false,
                }));
              }
            }
          }),
      );

      if (seq !== fetchSeqRef.current) return;

      const nextData = makeData();
      cacheData(nextData);
      setBackgroundLoadingLevels({});
      startTransition(() => {
        setData(nextData);
        setDataRevision((current) => current + 1);
      });
    } catch {
      requestFailed = true;
      if (seq !== fetchSeqRef.current) return;
      setError(true);
      if (scopePopupTargetRef.current) {
        setLevelAllPopup({
          kind: "error",
          message: `${scopePopupTargetRef.current.label}请求失败，请重试`,
        });
      }
      setData((prev) =>
        prev ? { ...prev, online: false, updatedAt: nowText() } : null,
      );
    } finally {
      if (seq === fetchSeqRef.current) setLoading(false);
      if (seq === fetchSeqRef.current) setPendingLevelAll(null);
      if (seq === fetchSeqRef.current) scopePopupTargetRef.current = null;
      if (seq === fetchSeqRef.current && !levelAllOverrideFailed && !requestFailed) {
        setLevelAllPopup((prev) => (prev?.kind === "loading" ? null : prev));
      }
    }
  }, [
    drillTarget?.areaId,
    drillTarget?.levelType,
    queryCacheKey,
    parentChangeWindows,
    requestedIndicatorCodes,
    scopeMode,
    dayLevelAllMode.GRID,
    dayLevelAllMode.CHANNEL,
    monthLevelAllMode.GRID,
    monthLevelAllMode.CHANNEL,
  ]);

  useEffect(() => {
    if (mode === "multi" && !multiSelectedCodes.length) {
      const initialCodes = selectableIndicators
        .slice(0, 6)
        .map((item) => item.code);
      if (initialCodes.length) {
        setMultiSelectedCodes(initialCodes);
        return;
      }
    }
    void fetchData(false);
    const timer = window.setInterval(() => {
      void getDashboardLatestRun()
        .then((result) => {
          if (
            !latestDataVersionRef.current ||
            result.data_version !== latestDataVersionRef.current
          ) {
            latestDataVersionRef.current = result.data_version;
            queryCacheRef.current.clear();
            return fetchData(true);
          }
        })
        .catch(() => fetchData(true));
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [fetchData, mode, multiSelectedCodes.length, selectableIndicators.length]);

  useEffect(() => {
    if (levelAllPopup?.kind !== "error") return;
    const timer = window.setTimeout(() => {
      setLevelAllPopup((prev) => (prev?.kind === "error" ? null : prev));
    }, 3500);
    return () => window.clearTimeout(timer);
  }, [levelAllPopup]);

  /* auto‑select first indicator when data first arrives */
  useEffect(() => {
    if (selectableIndicators.length) {
      setIndicator((prev) =>
        selectableIndicators.some((ind) => ind.code === prev)
          ? prev
          : selectableIndicators[0].code,
      );
    }
  }, [selectableIndicators]);

  /* derive boards from data + indicator + visible */
  const activeCode = indicator || data?.indicators[0]?.code || "";
  const activeIndicatorDataPending = Boolean(
    mode === "single" &&
    activeCode &&
    data &&
    !data.indicators.some((item) => item.code === activeCode),
  );
  const handleIndicatorChange = useCallback((code: string) => {
    if (code === indicator) return;
    setLoading(true);
    setError(false);
    setIndicator(code);
  }, [indicator]);

  const { dayLevels, monthLevels } = useMemo(() => {
    if (!data) return { dayLevels: [] as LevelBoard[], monthLevels: [] as LevelBoard[] };
    const dayOverrides = Object.fromEntries(
      (["GRID", "CHANNEL"] as LevelKey[])
        .filter((level) => scopeMode === "all" || dayLevelAllMode[level])
        .map((level) => [level, data.levelOverrides[level]]),
    ) as Partial<Record<LevelKey, LevelOverrideData>>;
    const monthOverrides = Object.fromEntries(
      (["GRID", "CHANNEL"] as LevelKey[])
        .filter((level) => scopeMode === "all" || monthLevelAllMode[level])
        .map((level) => [level, data.levelOverrides[level]]),
    ) as Partial<Record<LevelKey, LevelOverrideData>>;
    const changesRows = applyLevelOverrides(
      data.changesRows,
      dayOverrides,
      "changesRows",
    );
    const accRows = applyLevelOverrides(
      data.accRows,
      monthOverrides,
      "accRows",
    );
    return buildBoards(
      changesRows,
      accRows,
      activeCode,
      visible,
      normalizedDrillStack.length === 0 && scopeMode === "default",
      (["GRID", "CHANNEL"] as LevelKey[]).filter((level) => dayLevelAllMode[level]),
      (["GRID", "CHANNEL"] as LevelKey[]).filter((level) => monthLevelAllMode[level]),
      changeWindows,
    );
  }, [
    data,
    activeCode,
    visible,
    normalizedDrillStack.length,
    scopeMode,
    changeWindows,
    dayLevelAllMode.GRID,
    dayLevelAllMode.CHANNEL,
    monthLevelAllMode.GRID,
    monthLevelAllMode.CHANNEL,
  ]);

  useEffect(() => {
    setDaySorts((prev) => normalizeSorts(prev, changeWindows));
    setMonthSorts((prev) => normalizeSorts(prev, changeWindows));
  }, [changeWindows]);

  /* measure branch panel height */
  useEffect(() => {
    if (!branchPanelRef.current) {
      // DOM not ready, wait for next tick
      const timer = setTimeout(() => {
        if (branchPanelRef.current) {
          setBranchHeight(branchPanelRef.current.offsetHeight);
        }
      }, 100);
      return () => clearTimeout(timer);
    }
    setBranchHeight(branchPanelRef.current.offsetHeight);
    const observer = new ResizeObserver(() => {
      if (branchPanelRef.current) {
        setBranchHeight(branchPanelRef.current.offsetHeight);
      }
    });
    observer.observe(branchPanelRef.current);
    return () => observer.disconnect();
  }, [dayLevels.length, data?.latestRun]);

  /* measure month branch panel height */
  useEffect(() => {
    if (!monthBranchPanelRef.current) {
      const timer = setTimeout(() => {
        if (monthBranchPanelRef.current) {
          setMonthBranchHeight(monthBranchPanelRef.current.offsetHeight);
        }
      }, 100);
      return () => clearTimeout(timer);
    }
    setMonthBranchHeight(monthBranchPanelRef.current.offsetHeight);
    const observer = new ResizeObserver(() => {
      if (monthBranchPanelRef.current) {
        setMonthBranchHeight(monthBranchPanelRef.current.offsetHeight);
      }
    });
    observer.observe(monthBranchPanelRef.current);
    return () => observer.disconnect();
  }, [monthLevels.length, data?.latestRun]);

  /* handlers */
  const handleDrill = useCallback((row: BoardRow, levelType: string) => {
    if (levelType === "CHANNEL") return;
    setScopeMode("default");
    setDayLevelAllMode({});
    setMonthLevelAllMode({});
    const nextEntry = { areaId: row.areaId, areaName: row.name, levelType };
    if (levelType === "GRID") {
      const parentBranch = data?.changesRows.find(
        (item) => item.level_type === "BRANCH" && item.area_id === row.parentId,
      );
      const nextStack = parentBranch
        ? [
            {
              areaId: parentBranch.area_id,
              areaName: parentBranch.area_name,
              levelType: parentBranch.level_type,
            },
            nextEntry,
          ]
        : [nextEntry];
      setDrillStack(nextStack);
      return;
    }
    setDrillStack((prev) => {
      const last = prev[prev.length - 1];
      if (
        last &&
        last.areaId === nextEntry.areaId &&
        last.levelType === nextEntry.levelType
      ) {
        return prev;
      }
      if (levelType === "BRANCH") {
        return [nextEntry];
      }
      return [...prev, nextEntry];
    });
  }, [data?.changesRows]);

  const handleScopeChange = useCallback((value: string) => {
    setDayLevelAllMode({});
    setMonthLevelAllMode({});
    if (value === "__default__") {
      scopePopupTargetRef.current = { label: "郑州市 / 中原区" };
      setLevelAllPopup({
        kind: "loading",
        message: "正在请求郑州市 / 中原区...",
      });
      setScopeMode("default");
      setDrillStack([]);
      return;
    }
    if (value === "__all__") {
      scopePopupTargetRef.current = { label: "郑州市 / 全部" };
      setLevelAllPopup({
        kind: "loading",
        message: "正在请求郑州市 / 全部...",
      });
      setScopeMode("all");
      setDrillStack([]);
      return;
    }
    const [levelType, rawAreaId] = value.split(":");
    const areaId = Number(rawAreaId);
    if (levelType !== "BRANCH" || !Number.isFinite(areaId)) return;
    const branch = data?.changesRows.find(
      (item) => item.level_type === "BRANCH" && item.area_id === areaId,
    );
    if (!branch) return;
    const branchLabel = `郑州市 / ${branch.area_name}`;
    scopePopupTargetRef.current = { label: branchLabel };
    setLevelAllPopup({
      kind: "loading",
      message: `正在请求${branchLabel}...`,
    });
    setScopeMode("default");
    setDrillStack([{
      areaId: branch.area_id,
      areaName: branch.area_name,
      levelType: branch.level_type,
    }]);
  }, [data?.changesRows]);

  const handleToggleDayLevelAll = useCallback((level: LevelKey) => {
    if (level === "BRANCH") return;
    const nextActive = !dayLevelAllMode[level];
    setPendingLevelAll({
      section: "day",
      level,
      label: nextActive
        ? level === "GRID" ? "全部网格" : "全部渠道"
        : "当前范围",
    });
    setLevelAllPopup({
      kind: "loading",
      message: nextActive
        ? `正在请求${level === "GRID" ? "全部网格" : "全部渠道"}...`
        : "正在切回当前范围...",
    });
    setDayLevelAllMode((prev) => ({
      ...prev,
      [level]: !prev[level],
    }));
  }, [dayLevelAllMode]);

  const handleToggleMonthLevelAll = useCallback((level: LevelKey) => {
    if (level === "BRANCH") return;
    const nextActive = !monthLevelAllMode[level];
    setPendingLevelAll({
      section: "month",
      level,
      label: nextActive
        ? level === "GRID" ? "全部网格" : "全部渠道"
        : "当前范围",
    });
    setLevelAllPopup({
      kind: "loading",
      message: nextActive
        ? `正在请求${level === "GRID" ? "全部网格" : "全部渠道"}...`
        : "正在切回当前范围...",
    });
    setMonthLevelAllMode((prev) => ({
      ...prev,
      [level]: !prev[level],
    }));
  }, [monthLevelAllMode]);

  const updateChangeWindow = useCallback((index: number, minutes: number) => {
    setChangeWindows((prev) => {
      const normalized = normalizeChangeWindowMinutes(minutes, prev[index] || 5);
      if (prev.some((value, valueIndex) => valueIndex !== index && value === normalized)) {
        return prev;
      }
      return prev.map((value, valueIndex) => valueIndex === index ? normalized : value);
    });
  }, []);

  /* derive */
  const nextCollect = useMemo(() => {
    return nextFiveMinuteTimeText(data?.latestRun?.finished_at);
  }, [data?.latestRun?.finished_at]);

  /* ── render ── */
  return (
    <div className="cockpit-shell">
      <Header
        mode={mode}
        onModeChange={handleModeChange}
        drillName={orgScopeName}
        scopeValue={
          drillTarget?.levelType === "BRANCH"
            ? `BRANCH:${drillTarget.areaId}`
            : normalizedDrillStack.length > 0
              ? "__current__"
              : scopeMode === "all"
                ? "__all__"
                : "__default__"
        }
        scopeOptions={[
          ...(normalizedDrillStack.length > 0
            ? [{ label: orgScopeName, value: "__current__" }]
            : []),
          { label: "郑州市 / 中原区", value: "__default__" },
          { label: "郑州市 / 全部", value: "__all__" },
          ...(data?.changesRows || [])
            .filter((row) => row.level_type === "BRANCH")
            .map((row) => ({
              label: `郑州市 / ${row.area_name}`,
              value: `BRANCH:${row.area_id}`,
            })),
        ]}
        onScopeChange={handleScopeChange}
        activeCode={activeCode}
        indicators={selectableIndicators}
        onIndicatorChange={handleIndicatorChange}
        changeWindows={changeWindows}
        onChangeWindow={updateChangeWindow}
        online={data?.online ?? false}
        updatedAt={data?.updatedAt ?? nowText()}
        loading={loading}
        onRefresh={() => void fetchData(true)}
        onOpenCustomManager={() => setCustomManagerOpen(true)}
      />

      {customManagerOpen && (
        <CustomIndicatorManager
          onClose={() => setCustomManagerOpen(false)}
          onSaved={() => void fetchData(true)}
        />
      )}

      {levelAllPopup && (
        <div
          className={`cockpit-toast ${levelAllPopup.kind}`}
          role={levelAllPopup.kind === "error" ? "alert" : "status"}
        >
          {levelAllPopup.kind === "loading" && <RefreshCw size={15} className="spin" />}
          <span>{levelAllPopup.message}</span>
        </div>
      )}

      {mode === "single" ? (
      <>
      <main className="board-grid" style={{ marginBottom: 14 }}>
        <div className="section-title" style={{ gridColumn: "1 / -1", marginBottom: -14 }}>
          <strong>当日实时</strong><span>展示每30秒刷新，数据按采集批次更新</span>
        </div>
        {dayLevels.map((level) => (
          <LevelPanel
            key={level.key}
            level={level}
            sortKey={daySorts[level.key]}
            onSortChange={(k) => setDaySorts((prev) => ({ ...prev, [level.key]: k as SortKey }))}
            changeWindows={changeWindows}
            onDrill={handleDrill}
            levelAllActive={scopeMode === "all" || Boolean(dayLevelAllMode[level.key])}
            levelAllPending={
              Boolean(backgroundLoadingLevels[level.key]) || (
                loading &&
                pendingLevelAll?.section === "day" &&
                pendingLevelAll.level === level.key
              )
            }
            staleIndicatorData={activeIndicatorDataPending}
            queryLoading={loading}
            onToggleLevelAll={
              level.key !== "BRANCH" && scopeMode !== "all"
                ? handleToggleDayLevelAll
                : undefined
            }
            isBranch={level.key === "BRANCH"}
            branchHeight={branchHeight}
            branchPanelRef={level.key === "BRANCH" ? branchPanelRef : undefined}
          />
        ))}
      </main>

      {SHOW_MONTH_ACCUMULATION && monthLevels.length > 0 && (
        <main className="board-grid">
          <div className="section-title" style={{ gridColumn: "1 / -1", marginBottom: -14 }}>
            <strong>当月累计</strong><span>前一日期累计 + 当日实时</span>
          </div>
          {monthLevels.map((level) => (
            <LevelPanel
              key={level.key}
              level={level}
              sortKey={monthSorts[level.key]}
              onSortChange={(k) => setMonthSorts((prev) => ({ ...prev, [level.key]: k as SortKey }))}
              changeWindows={changeWindows}
              onDrill={handleDrill}
              levelAllActive={Boolean(monthLevelAllMode[level.key])}
              levelAllPending={
                loading &&
                pendingLevelAll?.section === "month" &&
                pendingLevelAll.level === level.key
              }
              staleIndicatorData={activeIndicatorDataPending}
              queryLoading={loading}
              onToggleLevelAll={level.key !== "BRANCH" ? handleToggleMonthLevelAll : undefined}
              isBranch={level.key === "BRANCH"}
              branchHeight={monthBranchHeight}
              branchPanelRef={level.key === "BRANCH" ? monthBranchPanelRef : undefined}
            />
          ))}
        </main>
      )}

      <Footer
        batchNo={data?.latestRun?.batch_no}
        online={data?.online ?? false}
        hasData={Boolean(data?.latestRun?.finished_at)}
        nextCollect={nextCollect}
      />
      </>
      ) : (
        <MultiMetricMatrix
          catalog={data?.indicatorCatalog || []}
          availableIndicators={data?.indicators || []}
          selectedCodes={multiSelectedCodes}
          onSelectedCodesChange={setMultiSelectedCodes}
          windowMinutes={multiMetricWindow}
          onWindowChange={setMultiMetricWindow}
          scopeMode={scopeMode}
          parentId={drillTarget?.areaId}
          parentLevel={drillTarget?.levelType}
          refreshKey={dataRevision}
          drillLevel={drillTarget?.levelType}
          onDrill={handleDrill}
        />
      )}
    </div>
  );
}

/* ── sub-components ── */

function Header({
  mode,
  onModeChange,
  drillName,
  scopeValue,
  scopeOptions,
  onScopeChange,
  activeCode,
  indicators,
  onIndicatorChange,
  changeWindows,
  onChangeWindow,
  online,
  updatedAt,
  loading,
  onRefresh,
  onOpenCustomManager,
}: {
  mode: CockpitMode;
  onModeChange: (mode: CockpitMode) => void;
  drillName: string;
  scopeValue: string;
  scopeOptions: { label: string; value: string }[];
  onScopeChange: (value: string) => void;
  activeCode: string;
  indicators: DashboardIndicator[];
  onIndicatorChange: (code: string) => void;
  changeWindows: number[];
  onChangeWindow: (index: number, minutes: number) => void;
  online: boolean;
  updatedAt: string;
  loading: boolean;
  onRefresh: () => void;
  onOpenCustomManager: () => void;
}) {
  const indOptions = indicators.map((ind) => ({ label: ind.name || ind.code, value: ind.code }));

  return (
    <header className="cockpit-header">
      <div className="brand">
        <div className="brand-icon"><BarChart3 size={28} /></div>
        <div>
          <div className="brand-title">数据驾驶舱</div>
          <div className="cockpit-mode-tabs" aria-label="驾驶舱模式">
            <button
              type="button"
              className={mode === "single" ? "active" : ""}
              onClick={() => onModeChange("single")}
            >
              单指标驾驶舱
            </button>
            <button
              type="button"
              className={mode === "multi" ? "active" : ""}
              onClick={() => onModeChange("multi")}
            >
              多指标驾驶舱
            </button>
          </div>
        </div>
      </div>
      <div className={mode === "single" ? "toolbar" : "toolbar toolbar-muted"}>
        <Selector
          label="组织范围"
          value={scopeValue}
          displayValue={drillName}
          options={scopeOptions}
          onChange={onScopeChange}
        />
        {mode === "single" && <Selector
          label="指标"
          value={activeCode}
          options={indOptions.length ? indOptions : undefined}
          onChange={onIndicatorChange}
        />}
        {mode === "single" && <WindowSelector
          windows={changeWindows}
          onChange={onChangeWindow}
        />}
      </div>
      <div className="header-status">
        <span className={online ? "pulse-dot" : "pulse-dot muted"} />
        <span>{online ? "实时采集中" : "等待数据"}</span>
        <span className="divider" />
        <span>更新时间</span>
        <strong>{updatedAt}</strong>
        <button className="refresh-button" onClick={onRefresh}>
          <RefreshCw size={15} className={loading ? "spin" : ""} />
          刷新
        </button>
        <button className="refresh-button" onClick={onOpenCustomManager}>
          <Settings size={15} />
          指标管理
        </button>
      </div>
    </header>
  );
}

type CustomIndicatorDraft = {
  code: string;
  name: string;
  enabled: boolean;
  components: Array<{
    source_code: string;
    coefficient: string;
    source_storage_mode: StorageMode;
  }>;
};

function emptyCustomDraft(): CustomIndicatorDraft {
  return {
    code: "",
    name: "",
    enabled: true,
    components: [{ source_code: "", coefficient: "1", source_storage_mode: "COMPONENT" }],
  };
}

function draftFromCustomIndicator(indicator: DashboardCustomIndicator): CustomIndicatorDraft {
  return {
    code: indicator.code,
    name: indicator.name,
    enabled: indicator.enabled,
    components: indicator.components.length
      ? indicator.components.map((component) => ({
          source_code: component.source_code,
          coefficient: String(component.coefficient ?? 1),
          source_storage_mode: component.source_storage_mode || "COMPONENT",
        }))
      : emptyCustomDraft().components,
  };
}

function CustomIndicatorManager({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [catalog, setCatalog] = useState<DashboardCatalogIndicator[]>([]);
  const [customIndicators, setCustomIndicators] = useState<DashboardCustomIndicator[]>([]);
  const [draft, setDraft] = useState<CustomIndicatorDraft>(() => emptyCustomDraft());
  const [message, setMessage] = useState("");
  const [managerTab, setManagerTab] = useState<"custom" | "source">("custom");
  const [sourceKeyword, setSourceKeyword] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilterMode>("all");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setMessage("");
    try {
      const [catalogData, customData] = await Promise.all([
        getDashboardIndicators(true, false),
        getDashboardCustomIndicators(),
      ]);
      setCatalog(catalogData.indicators);
      setCustomIndicators(customData.indicators);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sourceOptions = useMemo(
    () => catalog
      .filter((indicator) => indicator.indicator_type !== "CUSTOM")
      .map((indicator) => ({
        code: indicator.code,
        name: indicator.name || indicator.code,
      })),
    [catalog],
  );
  const sourceIndicators = useMemo(() => {
    const keyword = sourceKeyword.trim().toLowerCase();
    return catalog
      .filter((indicator) => indicator.indicator_type !== "CUSTOM")
      .filter((indicator) => {
        if (sourceFilter === "enabled") return indicator.enabled;
        if (sourceFilter === "disabled") return !indicator.enabled;
        if (sourceFilter === "store") return indicator.storage_mode === "STORE";
        if (sourceFilter === "component") return indicator.storage_mode === "COMPONENT";
        return true;
      })
      .filter((indicator) =>
        !keyword ||
        indicator.name.toLowerCase().includes(keyword) ||
        indicator.code.toLowerCase().includes(keyword),
      );
  }, [catalog, sourceFilter, sourceKeyword]);
  const sourceStats = useMemo(() => {
    const sourceRows = catalog.filter((indicator) => indicator.indicator_type !== "CUSTOM");
    return {
      total: sourceRows.length,
      enabled: sourceRows.filter((indicator) => indicator.enabled).length,
      disabled: sourceRows.filter((indicator) => !indicator.enabled).length,
      store: sourceRows.filter((indicator) => indicator.storage_mode === "STORE").length,
      component: sourceRows.filter((indicator) => indicator.storage_mode === "COMPONENT").length,
    };
  }, [catalog]);

  const updateComponent = (
    index: number,
    patch: Partial<CustomIndicatorDraft["components"][number]>,
  ) => {
    setDraft((current) => ({
      ...current,
      components: current.components.map((component, componentIndex) =>
        componentIndex === index ? { ...component, ...patch } : component,
      ),
    }));
  };

  const addComponent = () => {
    setDraft((current) => ({
      ...current,
      components: [
        ...current.components,
        { source_code: "", coefficient: "1", source_storage_mode: "COMPONENT" },
      ],
    }));
  };

  const removeComponent = (index: number) => {
    setDraft((current) => ({
      ...current,
      components: current.components.filter((_, componentIndex) => componentIndex !== index),
    }));
  };

  const save = async () => {
    const code = draft.code.trim();
    const name = draft.name.trim();
    const components = draft.components
      .map((component) => ({
        source_code: component.source_code.trim(),
        coefficient: Number(component.coefficient || 1),
        source_storage_mode: component.source_storage_mode,
      }))
      .filter((component) => component.source_code);
    if (!code || !name || !components.length) {
      setMessage("请填写编码、名称，并至少选择 1 个源指标");
      return;
    }
    if (components.some((component) => !Number.isFinite(component.coefficient))) {
      setMessage("系数必须是有效数字");
      return;
    }
    if (draft.components.some((component) => {
      const coefficient = component.coefficient.trim();
      return coefficient && !COEFFICIENT_PATTERN.test(coefficient);
    })) {
      setMessage("系数最多保留 4 位小数");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await saveDashboardCustomIndicator({ code, name, enabled: draft.enabled, components });
      setDraft(emptyCustomDraft());
      await load();
      onSaved();
      setMessage("已保存自定义指标");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const removeCustom = async (indicator: DashboardCustomIndicator) => {
    if (!window.confirm(`确定删除自定义指标「${indicator.name}」吗？`)) return;
    setBusy(true);
    setMessage("");
    try {
      await deleteDashboardCustomIndicator(indicator.code);
      if (draft.code === indicator.code) setDraft(emptyCustomDraft());
      await load();
      onSaved();
      setMessage("已删除自定义指标");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const updateSourceIndicator = async (
    indicator: DashboardCatalogIndicator,
    patch: { enabled?: boolean; storage_mode?: StorageMode },
  ) => {
    const actionText = patch.enabled != null
      ? `${patch.enabled ? "启用" : "停用"}源指标「${indicator.name}」`
      : `将源指标「${indicator.name}」改为${
          patch.storage_mode === "STORE" ? "落库展示" : "只参与计算"
        }`;
    if (!window.confirm(`确定要${actionText}吗？`)) return;
    setBusy(true);
    setMessage("");
    try {
      await updateDashboardIndicatorSettings(indicator.code, patch);
      await load();
      onSaved();
      setMessage("已保存源指标设置");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="custom-metric-backdrop" role="dialog" aria-modal="true">
      <section className="custom-metric-modal">
        <header className="custom-metric-head">
          <div>
            <strong>自定义指标管理</strong>
            <span>维护自定义指标公式、源指标启停和落库方式</span>
          </div>
          <button type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="custom-metric-body">
          <aside className="custom-metric-list">
            <div className="custom-manager-tabs">
              <button
                type="button"
                className={managerTab === "custom" ? "active" : ""}
                onClick={() => setManagerTab("custom")}
              >
                自定义指标
              </button>
              <button
                type="button"
                className={managerTab === "source" ? "active" : ""}
                onClick={() => setManagerTab("source")}
              >
                源指标
              </button>
            </div>
            {managerTab === "custom" ? (
            <>
            <button type="button" onClick={() => setDraft(emptyCustomDraft())}>
              <Plus size={15} />
              新建自定义指标
            </button>
            {customIndicators.map((indicator) => (
              <div key={indicator.code} className="custom-metric-item">
                <button type="button" onClick={() => setDraft(draftFromCustomIndicator(indicator))}>
                  <strong>{indicator.name}</strong>
                  <span>{indicator.code}</span>
                </button>
                <button type="button" title="删除" onClick={() => void removeCustom(indicator)}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
            </>
            ) : (
              <div className="source-manager-summary">
                <strong>{sourceIndicators.length}</strong>
                <span>当前列表</span>
                <dl>
                  <div>
                    <dt>全部</dt>
                    <dd>{sourceStats.total}</dd>
                  </div>
                  <div>
                    <dt>启用</dt>
                    <dd>{sourceStats.enabled}</dd>
                  </div>
                  <div>
                    <dt>落库</dt>
                    <dd>{sourceStats.store}</dd>
                  </div>
                  <div>
                    <dt>只计算</dt>
                    <dd>{sourceStats.component}</dd>
                  </div>
                </dl>
              </div>
            )}
          </aside>
          <main className="custom-metric-form">
            {managerTab === "source" ? (
              <>
                <div className="source-manager-toolbar">
                  <label className="source-manager-search">
                    <Search size={15} />
                    <input
                      value={sourceKeyword}
                      onChange={(event) => setSourceKeyword(event.target.value)}
                      placeholder="搜索源指标名称或编码"
                    />
                  </label>
                  <div className="source-filter-tabs" aria-label="源指标筛选">
                    {[
                      ["all", "全部", sourceStats.total],
                      ["enabled", "已启用", sourceStats.enabled],
                      ["disabled", "未启用", sourceStats.disabled],
                      ["store", "落库", sourceStats.store],
                      ["component", "只计算", sourceStats.component],
                    ].map(([key, label, count]) => (
                      <button
                        key={key}
                        type="button"
                        className={sourceFilter === key ? "active" : ""}
                        onClick={() => setSourceFilter(key as SourceFilterMode)}
                      >
                        {label}
                        <span>{count}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="source-manager-list">
                  {sourceIndicators.map((indicator) => (
                    <div
                      key={indicator.code}
                      className={[
                        "source-manager-row",
                        indicator.enabled ? "is-enabled" : "is-disabled",
                        indicator.storage_mode === "COMPONENT" ? "is-component" : "",
                      ].filter(Boolean).join(" ")}
                    >
                      <div className="source-manager-name">
                        <strong>{indicator.name}</strong>
                        <span>{indicator.code}</span>
                      </div>
                      <div className="source-manager-status">
                        <span>{indicator.enabled ? "已启用" : "未启用"}</span>
                        <span>{indicator.storage_mode === "STORE" ? "落库展示" : "只参与计算"}</span>
                      </div>
                      <label className="source-manager-enable">
                        <input
                          type="checkbox"
                          checked={indicator.enabled}
                          disabled={busy}
                          onChange={(event) => void updateSourceIndicator(indicator, {
                            enabled: event.target.checked,
                          })}
                        />
                        启用
                      </label>
                      <select
                        value={indicator.storage_mode}
                        disabled={busy}
                        onChange={(event) => void updateSourceIndicator(indicator, {
                          storage_mode: event.target.value as StorageMode,
                        })}
                      >
                        <option value="STORE">落库展示</option>
                        <option value="COMPONENT">只参与计算</option>
                      </select>
                    </div>
                  ))}
                  {!sourceIndicators.length && (
                    <div className="source-manager-empty">
                      没有匹配的源指标
                    </div>
                  )}
                </div>
                {message && <div className="custom-metric-message">{message}</div>}
              </>
            ) : (
            <>
            <div className="custom-metric-grid">
              <label>
                <span>编码</span>
                <input
                  value={draft.code}
                  onChange={(event) => setDraft((current) => ({ ...current, code: event.target.value }))}
                  placeholder="custom_aijia"
                />
              </label>
              <label>
                <span>名称</span>
                <input
                  value={draft.name}
                  onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                  placeholder="爱家亲情网"
                />
              </label>
              <label className="custom-metric-toggle">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                />
                <span>启用</span>
              </label>
            </div>
            <div className="custom-component-head">
              <strong>组成指标</strong>
              <button type="button" onClick={addComponent}>
                <Plus size={14} />
                添加
              </button>
            </div>
            <div className="custom-component-list">
              {draft.components.map((component, index) => (
                <div key={index} className="custom-component-row">
                  <SourceMetricPicker
                    value={component.source_code}
                    options={sourceOptions}
                    onChange={(value) => updateComponent(index, { source_code: value })}
                  />
                  <input
                    value={component.coefficient}
                    onChange={(event) => {
                      const nextValue = event.target.value.trim();
                      if (COEFFICIENT_INPUT_PATTERN.test(nextValue)) {
                        updateComponent(index, { coefficient: nextValue });
                      }
                    }}
                    inputMode="decimal"
                    placeholder="1"
                  />
                  <select
                    value={component.source_storage_mode}
                    onChange={(event) => updateComponent(index, {
                      source_storage_mode: event.target.value as StorageMode,
                    })}
                  >
                    <option value="COMPONENT">只参与计算</option>
                    <option value="STORE">源指标也落库</option>
                  </select>
                  <button
                    type="button"
                    title="移除"
                    disabled={draft.components.length <= 1}
                    onClick={() => removeComponent(index)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
            {message && <div className="custom-metric-message">{message}</div>}
            <div className="custom-metric-actions">
              <button type="button" onClick={() => setDraft(emptyCustomDraft())}>清空</button>
              <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
                {busy ? "处理中..." : "保存指标"}
              </button>
            </div>
            </>
            )}
          </main>
        </div>
      </section>
    </div>
  );
}

function SourceMetricPicker({
  value,
  options,
  onChange,
}: {
  value: string;
  options: SourceMetricOption[];
  onChange: (value: string) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const selected = options.find((option) => option.code === value);
  const normalizedKeyword = keyword.trim().toLowerCase();
  const filteredOptions = normalizedKeyword
    ? options.filter((option) =>
        option.name.toLowerCase().includes(normalizedKeyword) ||
        option.code.toLowerCase().includes(normalizedKeyword),
      )
    : options;

  return (
    <details className="source-metric-picker">
      <summary>
        <span title={selected ? `${selected.name} (${selected.code})` : "选择源指标"}>
          {selected ? selected.name : "选择源指标"}
        </span>
        {selected && <em>{selected.code}</em>}
        <ChevronDown size={14} />
      </summary>
      <div className="source-metric-popover">
        <label className="source-metric-search">
          <Search size={14} />
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索名称或编码"
          />
        </label>
        <div className="source-metric-options">
          {filteredOptions.map((option) => (
            <button
              key={option.code}
              type="button"
              className={option.code === value ? "active" : ""}
              onClick={(event) => {
                onChange(option.code);
                setKeyword("");
                event.currentTarget.closest("details")?.removeAttribute("open");
              }}
            >
              <strong>{option.name}</strong>
              <span>{option.code}</span>
            </button>
          ))}
          {!filteredOptions.length && (
            <div className="source-metric-empty">没有匹配的源指标</div>
          )}
        </div>
      </div>
    </details>
  );
}

function WindowSelector({
  windows,
  onChange,
}: {
  windows: number[];
  onChange: (index: number, minutes: number) => void;
}) {
  const [drafts, setDrafts] = useState(() => windows.map(String));

  useEffect(() => {
    setDrafts(windows.map(String));
  }, [windows]);

  const commit = (index: number) => {
    const current = windows[index] || 5;
    const normalized = normalizeChangeWindowMinutes(Number(drafts[index]), current);
    const committed = windows.some(
      (value, valueIndex) => valueIndex !== index && value === normalized,
    )
      ? current
      : normalized;
    onChange(index, committed);
    setDrafts((prev) =>
      prev.map((value, valueIndex) =>
        valueIndex === index ? String(committed) : value,
      ),
    );
  };

  return (
    <label className="selector window-selector">
      <span>变化窗口</span>
      <div style={{ display: "flex", gap: 6 }}>
        {windows.map((minutes, index) => (
          <input
            key={index}
            type="number"
            min={5}
            max={1440}
            step={5}
            value={drafts[index] ?? String(minutes)}
            onChange={(event) => {
              const next = event.target.value.replace(/[^\d]/g, "");
              setDrafts((prev) =>
                prev.map((value, valueIndex) =>
                  valueIndex === index ? next : value,
                ),
              );
            }}
            onBlur={() => commit(index)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.currentTarget.blur();
              }
            }}
            title="输入分钟数，保存时会按 5 分钟粒度归一"
          />
        ))}
      </div>
    </label>
  );
}

function SingleWindowInput({
  value,
  onChange,
}: {
  value: number;
  onChange: (minutes: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    const normalized = normalizeChangeWindowMinutes(Number(draft), value);
    setDraft(String(normalized));
    if (normalized !== value) {
      onChange(normalized);
    }
  };

  return (
    <label className="selector matrix-window-input">
      <span>变化窗口</span>
      <input
        type="number"
        min={5}
        max={1440}
        step={5}
        value={draft}
        onChange={(event) => setDraft(event.target.value.replace(/[^\d]/g, ""))}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
        }}
        aria-label="多指标变化窗口分钟数"
      />
      <em>分钟</em>
    </label>
  );
}

function MultiMetricMatrix({
  catalog,
  availableIndicators,
  selectedCodes,
  onSelectedCodesChange,
  windowMinutes,
  onWindowChange,
  scopeMode,
  parentId,
  parentLevel,
  refreshKey,
  drillLevel,
  onDrill,
}: {
  catalog: DashboardCatalogIndicator[];
  availableIndicators: DashboardIndicator[];
  selectedCodes: string[];
  onSelectedCodesChange: (codes: string[]) => void;
  windowMinutes: number;
  onWindowChange: (minutes: number) => void;
  scopeMode: ScopeMode;
  parentId?: number;
  parentLevel?: string;
  refreshKey: number;
  drillLevel?: string;
  onDrill: (row: BoardRow, levelType: string) => void;
}) {
  const orderedCatalog = useMemo(() => {
    const byCode = new Map(
      catalog
        .filter((indicator) => indicator.enabled)
        .map((indicator) => [indicator.code, indicator]),
    );
    for (const indicator of availableIndicators) {
      if (!byCode.has(indicator.code)) {
        byCode.set(indicator.code, {
          ...indicator,
          enabled: true,
          source_active: true,
          indicator_type: "SOURCE",
          storage_mode: "STORE",
          removed_at: null,
        });
      }
    }
    return [...byCode.values()]
      .sort((left, right) => left.sort_order - right.sort_order);
  }, [availableIndicators, catalog]);

  const [level, setLevel] = useState<LevelKey>("BRANCH");
  const [search, setSearch] = useState("");
  const [sortIndicator, setSortIndicator] = useState("");
  const [sortMode, setSortMode] = useState<MatrixSortMode>("doneDesc");
  const [indicatorSearch, setIndicatorSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [matrixRows, setMatrixRows] = useState<DashboardRowWithChanges[]>([]);
  const [matrixTotal, setMatrixTotal] = useState(0);
  const [matrixPage, setMatrixPage] = useState(1);
  const [matrixTotalPages, setMatrixTotalPages] = useState(0);
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixError, setMatrixError] = useState("");
  const [matrixScrollTop, setMatrixScrollTop] = useState(0);
  const [matrixViewportHeight, setMatrixViewportHeight] = useState(600);
  const matrixScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (drillLevel === "BRANCH") {
      setLevel("GRID");
    } else if (drillLevel === "GRID") {
      setLevel("CHANNEL");
    } else if (!drillLevel || drillLevel === "CITY") {
      setLevel("BRANCH");
    }
  }, [drillLevel]);

  useEffect(() => {
    if (!orderedCatalog.length) return;
    const availableCodeSet = new Set(orderedCatalog.map((indicator) => indicator.code));
    const valid = selectedCodes
      .filter((code) => availableCodeSet.has(code))
      .slice(0, 6);
    if (valid.length !== selectedCodes.length) {
      onSelectedCodesChange(
        valid.length ? valid : orderedCatalog.slice(0, 6).map((indicator) => indicator.code),
      );
    }
  }, [onSelectedCodesChange, orderedCatalog, selectedCodes]);

  useEffect(() => {
    if (!selectedCodes.length) return;
    if (!selectedCodes.includes(sortIndicator)) {
      setSortIndicator(selectedCodes[0]);
    }
  }, [selectedCodes, sortIndicator]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setMatrixPage(1);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    setMatrixPage(1);
  }, [level, parentId, parentLevel, scopeMode, selectedCodes, sortIndicator, sortMode, windowMinutes]);

  useEffect(() => {
    if (!selectedCodes.length || !sortIndicator) return;
    let cancelled = false;
    setMatrixLoading(true);
    setMatrixError("");
    void getDashboardMatrix({
      levelType: level,
      scopeMode,
      parentId,
      parentLevel,
      indicatorCodes: selectedCodes,
      changeWindow: windowMinutes,
      search: debouncedSearch || undefined,
      sortIndicator,
      sortMode,
      page: matrixPage,
      pageSize: 100,
    })
      .then((result) => {
        if (cancelled) return;
        setMatrixRows(result.rows);
        setMatrixTotal(result.total);
        setMatrixTotalPages(result.total_pages);
      })
      .catch((requestError) => {
        if (cancelled) return;
        setMatrixRows([]);
        setMatrixTotal(0);
        setMatrixTotalPages(0);
        setMatrixError(
          requestError instanceof Error ? requestError.message : "矩阵请求失败",
        );
      })
      .finally(() => {
        if (!cancelled) setMatrixLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    debouncedSearch,
    level,
    matrixPage,
    parentId,
    parentLevel,
    refreshKey,
    scopeMode,
    selectedCodes,
    sortIndicator,
    sortMode,
    windowMinutes,
  ]);

  const selectedIndicators = selectedCodes
    .map((code) => orderedCatalog.find((indicator) => indicator.code === code))
    .filter((indicator): indicator is DashboardCatalogIndicator => Boolean(indicator));
  const filteredOptions = orderedCatalog.filter((indicator) => {
    const keyword = indicatorSearch.trim().toLowerCase();
    return !keyword ||
      indicator.name.toLowerCase().includes(keyword) ||
      indicator.code.toLowerCase().includes(keyword);
  });

  const virtualRange = useMemo(() => {
    const visibleCount = Math.ceil(matrixViewportHeight / MATRIX_ROW_HEIGHT);
    const start = Math.max(
      0,
      Math.floor(matrixScrollTop / MATRIX_ROW_HEIGHT) - MATRIX_VIRTUAL_OVERSCAN,
    );
    const end = Math.min(
      matrixRows.length,
      start + visibleCount + MATRIX_VIRTUAL_OVERSCAN * 2,
    );
    return {
      start,
      end,
      rows: matrixRows.slice(start, end),
      topHeight: start * MATRIX_ROW_HEIGHT,
      bottomHeight: Math.max(0, (matrixRows.length - end) * MATRIX_ROW_HEIGHT),
    };
  }, [matrixRows, matrixScrollTop, matrixViewportHeight]);

  useEffect(() => {
    const container = matrixScrollRef.current;
    if (!container) return;
    setMatrixViewportHeight(container.clientHeight || 600);
    container.scrollTop = 0;
    setMatrixScrollTop(0);
  }, [level, search, sortIndicator, sortMode]);

  const toggleIndicator = (code: string) => {
    if (selectedCodes.includes(code)) {
      onSelectedCodesChange(
        selectedCodes.length === 1
          ? selectedCodes
          : selectedCodes.filter((item) => item !== code),
      );
      return;
    }
    if (selectedCodes.length < 6) {
      onSelectedCodesChange([...selectedCodes, code]);
    }
  };

  const handleMatrixDrill = (row: DashboardRowWithChanges) => {
    if (row.level_type === "CHANNEL") return;
    setLevel(row.level_type === "BRANCH" ? "GRID" : "CHANNEL");
    onDrill({
      areaId: row.area_id,
      parentId: row.parent_id,
      name: row.area_name,
      done: 0,
      target: null,
      changes: {},
    }, row.level_type);
  };

  return (
    <main className="multi-metric-view">
      <section className="matrix-toolbar">
        <Selector
          label="层级"
          value={level}
          options={[
            { label: "分公司级", value: "BRANCH" },
            { label: "网格级", value: "GRID" },
            { label: "渠道级", value: "CHANNEL" },
          ]}
          onChange={(value) => setLevel(value as LevelKey)}
        />
        <label className="matrix-search">
          <span>筛选</span>
          <Search size={15} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="区域名称或编码"
          />
        </label>
        <SingleWindowInput
          value={windowMinutes}
          onChange={onWindowChange}
        />
        <Selector
          label="排序指标"
          value={sortIndicator}
          options={selectedIndicators.map((indicator) => ({
            label: indicator.name,
            value: indicator.code,
          }))}
          onChange={setSortIndicator}
        />
        <MatrixSortPicker value={sortMode} onChange={setSortMode} />
        <details
          className="metric-multi-picker"
          onMouseEnter={keepDetailsOpen}
          onMouseLeave={closeDetailsAfterLeave}
        >
          <summary>
            <span>指标</span>
            <strong>多选 {selectedCodes.length} 项</strong>
            <ChevronDown size={15} />
          </summary>
          <div className="metric-picker-popover">
            <label className="metric-picker-search">
              <Search size={14} />
              <input
                value={indicatorSearch}
                onChange={(event) => setIndicatorSearch(event.target.value)}
                placeholder="搜索指标"
              />
            </label>
            <div className="metric-option-list">
              {filteredOptions.map((indicator) => (
                <label key={indicator.code} className="metric-check-option">
                  <input
                    type="checkbox"
                    checked={selectedCodes.includes(indicator.code)}
                    onChange={() => toggleIndicator(indicator.code)}
                  />
                  <span title={indicator.name}>{indicator.name}</span>
                </label>
              ))}
            </div>
            <div className="metric-picker-note">最多同时展示 6 个指标</div>
          </div>
        </details>
      </section>

      <div className="selected-indicator-strip">
        {selectedIndicators.map((indicator, index) => (
          <button
            key={indicator.code}
            type="button"
            onClick={() => toggleIndicator(indicator.code)}
            title="从矩阵中移除"
          >
            <i data-index={index} />
            {indicator.name}
          </button>
        ))}
      </div>

      <section className="matrix-panel">
        <div className="matrix-panel-head">
          <strong>区域 × 指标矩阵</strong>
          <span>{matrixTotal} 个区域，单元格展示完成值、目标值、完成率和 {windowMinutes} 分钟变化</span>
        </div>
        <div
          ref={matrixScrollRef}
          className="matrix-scroll"
          onScroll={(event) => {
            setMatrixScrollTop(event.currentTarget.scrollTop);
            setMatrixViewportHeight(event.currentTarget.clientHeight);
          }}
        >
          <table className="metric-matrix">
            <thead>
              <tr>
                <th className="matrix-area-column">区域</th>
                {selectedIndicators.map((indicator) => (
                  <th key={indicator.code}>{indicator.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {virtualRange.topHeight > 0 && (
                <tr className="matrix-virtual-spacer" aria-hidden="true">
                  <td
                    colSpan={selectedIndicators.length + 1}
                    style={{ height: virtualRange.topHeight }}
                  />
                </tr>
              )}
              {virtualRange.rows.map((row) => (
                <tr key={row.area_id}>
                  <td className="matrix-area-column">
                    <button
                      type="button"
                      disabled={level === "CHANNEL"}
                      onClick={() => handleMatrixDrill(row)}
                    >
                      <strong>{row.area_name}</strong>
                      <span>{row.area_code} · {levelLabel(row.level_type)}</span>
                    </button>
                  </td>
                  {selectedIndicators.map((indicator) => (
                    <MatrixMetricCell
                      key={indicator.code}
                      row={row}
                      indicator={indicator}
                      windowMinutes={windowMinutes}
                    />
                  ))}
                </tr>
              ))}
              {virtualRange.bottomHeight > 0 && (
                <tr className="matrix-virtual-spacer" aria-hidden="true">
                  <td
                    colSpan={selectedIndicators.length + 1}
                    style={{ height: virtualRange.bottomHeight }}
                  />
                </tr>
              )}
              {!matrixRows.length && (
                <tr>
                  <td
                    className="matrix-empty"
                    colSpan={selectedIndicators.length + 1}
                  >
                    {matrixLoading
                      ? "正在加载..."
                      : matrixError || "当前层级暂无数据"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {matrixTotalPages > 1 && (
          <div className="matrix-pagination">
            <button
              type="button"
              disabled={matrixPage <= 1 || matrixLoading}
              onClick={() => setMatrixPage((current) => Math.max(1, current - 1))}
            >
              上一页
            </button>
            <span>第 {matrixPage} / {matrixTotalPages} 页</span>
            <button
              type="button"
              disabled={matrixPage >= matrixTotalPages || matrixLoading}
              onClick={() => setMatrixPage((current) => current + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </section>
    </main>
  );
}

function MatrixMetricCell({
  row,
  indicator,
  windowMinutes,
}: {
  row: DashboardRowWithChanges;
  indicator: DashboardCatalogIndicator;
  windowMinutes: number;
}) {
  const done = row.metrics[indicator.code];
  const target = row.targets?.[indicator.code];
  const progress = done != null && target != null && target > 0 ? done / target : null;
  const change = row.changes?.[indicator.code]?.[`change_${windowMinutes}min`];
  const progressClass = progress == null
    ? "unknown"
    : progress >= 0.9
      ? "good"
      : progress >= 0.8
        ? "mid"
        : "low";
  const changeClass = (change?.value ?? 0) > 0
    ? "up"
    : (change?.value ?? 0) < 0
      ? "down"
      : "flat";

  return (
    <td className="matrix-metric-cell">
      <div className="matrix-cell-main">
        <strong>{done == null ? "--" : formatNumber(done)}</strong>
        <span className={progressClass}>
          {progress == null ? "--" : fmtPct(progress)}
        </span>
      </div>
      <div className="matrix-cell-meta">
        <span>目标 {target == null ? "--" : formatNumber(target)}</span>
        <span className={changeClass}>
          {change?.value == null ? "--" : signed(change.value)}
          {" / "}
          {change?.rate == null ? "--" : signedPct(change.rate * 100)}
        </span>
      </div>
    </td>
  );
}

function levelLabel(level: DashboardRow["level_type"]) {
  if (level === "BRANCH") return "分公司级";
  if (level === "GRID") return "网格级";
  if (level === "CHANNEL") return "渠道级";
  return "市级";
}

function Footer({
  batchNo,
  online,
  hasData,
  nextCollect,
}: {
  batchNo?: string;
  online: boolean;
  hasData: boolean;
  nextCollect: string;
}) {
  return (
    <footer className="cockpit-footer">
      <StatusItem icon={Activity} label="采集批次" value={batchNo || "暂无"} />
      <StatusItem icon={Signal} label="接口状态" value={online ? "接口正常" : "离线"} ok={online} />
      <StatusItem icon={Database} label="数据状态" value={hasData ? "有数据" : "无数据"} ok={hasData} />
      <StatusItem icon={Activity} label="下一次采集" value={nextCollect} />
    </footer>
  );
}

function Selector({
  label,
  value,
  displayValue,
  options,
  onChange,
}: {
  label: string;
  value: string;
  displayValue?: string;
  options?: { label: string; value: string }[];
  onChange?: (v: string) => void;
}) {
  const selectedLabel = options?.find((option) => option.value === value)?.label
    || displayValue
    || value;

  return (
    <div className="selector">
      <span>{label}</span>
      {options ? (
        <details
          className="selector-field"
          onMouseEnter={keepDetailsOpen}
          onMouseLeave={closeDetailsAfterLeave}
        >
          <summary>
            <strong>{selectedLabel}</strong>
            <ChevronDown size={16} />
          </summary>
          <div className="selector-popover">
            {displayValue && !options.some((option) => option.value === value) && (
              <button type="button" className="active">{displayValue}</button>
            )}
            {options.map((option) => (
              <button
                key={option.value}
                type="button"
                className={option.value === value ? "active" : ""}
                onClick={(event) => {
                  onChange?.(option.value);
                  event.currentTarget.closest("details")?.removeAttribute("open");
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
        </details>
      ) : (
        <strong>{displayValue || value}</strong>
      )}
    </div>
  );
}

const MATRIX_SORT_FIELDS: {
  key: "progress" | "done" | "changeValue" | "changeRate";
  label: string;
  asc: MatrixSortMode;
  desc: MatrixSortMode;
}[] = [
  { key: "progress", label: "完成率", asc: "progressAsc", desc: "progressDesc" },
  { key: "done", label: "完成值", asc: "doneAsc", desc: "doneDesc" },
  { key: "changeValue", label: "变化量", asc: "changeValueAsc", desc: "changeValueDesc" },
  { key: "changeRate", label: "变化率", asc: "changeRateAsc", desc: "changeRateDesc" },
];

function MatrixSortPicker({
  value,
  onChange,
}: {
  value: MatrixSortMode;
  onChange: (value: MatrixSortMode) => void;
}) {
  const selected = MATRIX_SORT_FIELDS.find(
    (field) => field.asc === value || field.desc === value,
  ) || MATRIX_SORT_FIELDS[1];
  const selectedDirection = selected.asc === value ? "asc" : "desc";
  const DirectionIcon = selectedDirection === "asc" ? ArrowUp : ArrowDown;

  return (
    <div className="matrix-sort-picker">
      <span>排序方式</span>
      <details
        className="matrix-sort-field-picker"
        onMouseEnter={keepDetailsOpen}
        onMouseLeave={closeDetailsAfterLeave}
      >
        <summary>
          <strong>{selected.label}</strong>
          <ChevronDown size={15} />
        </summary>
        <div className="matrix-sort-field-popover">
          {MATRIX_SORT_FIELDS.map((field) => (
            <button
              key={field.key}
              type="button"
              className={field.key === selected.key ? "active" : ""}
              onClick={(event) => {
                onChange(selectedDirection === "asc" ? field.asc : field.desc);
                event.currentTarget.closest("details")?.removeAttribute("open");
              }}
            >
              {field.label}
            </button>
          ))}
        </div>
      </details>
      <button
        type="button"
        className="matrix-sort-direction"
        title={selectedDirection === "asc" ? "当前升序，点击切换为降序" : "当前降序，点击切换为升序"}
        aria-label={selectedDirection === "asc" ? "切换为降序" : "切换为升序"}
        onClick={() => onChange(
          selectedDirection === "asc" ? selected.desc : selected.asc,
        )}
      >
        <DirectionIcon size={16} />
      </button>
    </div>
  );
}

function LevelPanel({
  level,
  sortKey,
  onSortChange,
  changeWindows,
  onDrill,
  levelAllActive,
  levelAllPending,
  staleIndicatorData,
  queryLoading,
  onToggleLevelAll,
  isBranch,
  branchHeight,
  branchPanelRef,
}: {
  level: LevelBoard;
  sortKey: SortKey;
  onSortChange: (k: SortKey) => void;
  changeWindows: number[];
  onDrill: (row: BoardRow, levelType: string) => void;
  levelAllActive?: boolean;
  levelAllPending?: boolean;
  staleIndicatorData?: boolean;
  queryLoading?: boolean;
  onToggleLevelAll?: (level: LevelKey) => void;
  isBranch?: boolean;
  branchHeight?: number;
  branchPanelRef?: React.RefObject<HTMLDivElement | null>;
}) {
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [tableScrollTop, setTableScrollTop] = useState(0);
  const [tableViewportHeight, setTableViewportHeight] = useState(600);
  const dataTableRef = useRef<HTMLDivElement>(null);
  const rows = useMemo(
    () => sortRows(level.dayRows, sortKey, sortDirection),
    [level.dayRows, sortDirection, sortKey],
  );
  const useVirtualRows = level.key === "CHANNEL" && Boolean(levelAllActive) && rows.length > 200;
  const virtualRows = useMemo(() => {
    if (!useVirtualRows) {
      return { start: 0, rows, topHeight: 0, bottomHeight: 0 };
    }
    const visibleCount = Math.ceil(tableViewportHeight / SINGLE_ROW_HEIGHT);
    const start = Math.max(
      0,
      Math.floor(tableScrollTop / SINGLE_ROW_HEIGHT) - SINGLE_VIRTUAL_OVERSCAN,
    );
    const end = Math.min(
      rows.length,
      start + visibleCount + SINGLE_VIRTUAL_OVERSCAN * 2,
    );
    return {
      start,
      rows: rows.slice(start, end),
      topHeight: start * SINGLE_ROW_HEIGHT,
      bottomHeight: Math.max(0, (rows.length - end) * SINGLE_ROW_HEIGHT),
    };
  }, [rows, tableScrollTop, tableViewportHeight, useVirtualRows]);
  const lastWindow = changeWindows[changeWindows.length - 1] ?? 60;

  const totalDone = rows.reduce((s, r) => s + r.done, 0);
  const targetedRows = rows.filter((r) => r.target != null && r.target > 0);
  const targetedDone = targetedRows.reduce((s, r) => s + r.done, 0);
  const totalTarget = targetedRows.reduce((s, r) => s + (r.target ?? 0), 0);
  const avgPct = totalTarget > 0 ? targetedDone / totalTarget : null;
  const lastChange = rows.reduce((s, r) => s + (r.changes[lastWindow]?.value ?? 0), 0);

  const needsScroll = branchHeight != null && !isBranch;
  const panelStyle: React.CSSProperties = needsScroll
    ? { height: branchHeight, overflow: "hidden" }
    : {};

  const dataTableStyle: React.CSSProperties = needsScroll
    ? { maxHeight: branchHeight - 43, overflowY: "auto" }
    : {};
  const levelAllLabel = level.key === "GRID" ? "全部网格" : "全部渠道";
  const showLoadingPlaceholder = (Boolean(staleIndicatorData) && Boolean(queryLoading))
    || (rows.length === 0 && Boolean(levelAllPending || queryLoading));
  const hideStaleRows = Boolean(staleIndicatorData);
  const visibleRows = hideStaleRows || showLoadingPlaceholder ? [] : virtualRows.rows;

  useEffect(() => {
    const container = dataTableRef.current;
    if (!container) return;
    setTableViewportHeight(container.clientHeight || 600);
    container.scrollTop = 0;
    setTableScrollTop(0);
  }, [levelAllActive, sortDirection, sortKey]);

  return (
    <section className="level-panel" ref={branchPanelRef} style={panelStyle}>
      <div className="panel-head">
        <div>
          <span className="panel-index">{level.index}</span>
          <span className="panel-title">{level.title}</span>
          <span className="panel-dot" />
          <span className="panel-count">
            {showLoadingPlaceholder ? "加载中" : `${level.total}项`}
          </span>
        </div>
        <div className="panel-actions">
          {onToggleLevelAll && (
            <button
              className={levelAllActive ? "level-reset-button active" : "level-reset-button"}
              type="button"
              disabled={levelAllPending}
              onClick={() => onToggleLevelAll(level.key)}
            >
              {levelAllActive ? "当前范围" : levelAllLabel}
            </button>
          )}
        <SingleMetricSortPicker
          value={sortKey}
          options={sortOptions(changeWindows)}
          direction={sortDirection}
          onChange={onSortChange}
          onDirectionChange={setSortDirection}
        />
        </div>
      </div>

      <div
        ref={dataTableRef}
        className="data-table"
        style={{ padding: "10px 10px 0", ...dataTableStyle }}
        onScroll={useVirtualRows ? (event) => {
          setTableScrollTop(event.currentTarget.scrollTop);
          setTableViewportHeight(event.currentTarget.clientHeight);
        } : undefined}
      >
        <div className="table-row table-head">
          <span>排名</span><span>名称</span>
          <span>完成</span>
          <span>目标</span>
          {changeWindows.map((minutes) => (
            <span key={minutes}>{minutes}分钟</span>
          ))}
        </div>
        {!hideStaleRows && !showLoadingPlaceholder && virtualRows.topHeight > 0 && (
          <div
            className="single-virtual-spacer"
            style={{ height: virtualRows.topHeight }}
            aria-hidden="true"
          />
        )}
        {visibleRows.map((r, i) => (
          <DataRow
            key={r.areaId}
            rank={virtualRows.start + i + 1}
            item={r}
            changeWindows={changeWindows}
            levelType={level.key}
            onDrill={onDrill}
          />
        ))}
        {!hideStaleRows && !showLoadingPlaceholder && virtualRows.bottomHeight > 0 && (
          <div
            className="single-virtual-spacer"
            style={{ height: virtualRows.bottomHeight }}
            aria-hidden="true"
          />
        )}
        {showLoadingPlaceholder && <LoadingRows />}
        {((rows.length === 0 || hideStaleRows) && !showLoadingPlaceholder) && (
          <EmptyRow message="暂无数据" />
        )}
      </div>

      <div className="section-total">
        <span>合计</span><strong>{hideStaleRows ? "--" : formatNumber(totalDone)}</strong>
        <span>平均进度</span><strong>{hideStaleRows ? "--" : fmtProgress(avgPct)}</strong>
        <span>{lastWindow}分钟</span>
        <strong className={lastChange >= 0 ? "up" : "down"}>
          {hideStaleRows ? "--" : signed(lastChange)}
        </strong>
      </div>
    </section>
  );
}

function LoadingRows() {
  return (
    <div className="loading-rows" aria-label="正在加载数据">
      {Array.from({ length: 8 }).map((_, index) => (
        <div className="loading-row" key={index}>
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

function SingleMetricSortPicker({
  value,
  options,
  direction,
  onChange,
  onDirectionChange,
}: {
  value: SortKey;
  options: { key: SortKey; label: string }[];
  direction: "asc" | "desc";
  onChange: (value: SortKey) => void;
  onDirectionChange: (direction: "asc" | "desc") => void;
}) {
  const selected = options.find((option) => option.key === value) || options[0];
  const DirectionIcon = direction === "asc" ? ArrowUp : ArrowDown;

  return (
    <div className="single-sort-picker">
      <details
        className="single-sort-field"
        onMouseEnter={keepDetailsOpen}
        onMouseLeave={closeDetailsAfterLeave}
      >
        <summary>
          <strong>{selected?.label || "完成量"}</strong>
          <ChevronDown size={14} />
        </summary>
        <div className="single-sort-popover">
          {options.map((option) => (
            <button
              key={option.key}
              type="button"
              className={option.key === value ? "active" : ""}
              onClick={(event) => {
                onChange(option.key);
                event.currentTarget.closest("details")?.removeAttribute("open");
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </details>
      <button
        type="button"
        className="single-sort-direction"
        title={direction === "asc" ? "当前升序，点击切换为降序" : "当前降序，点击切换为升序"}
        aria-label={direction === "asc" ? "切换为降序" : "切换为升序"}
        onClick={() => onDirectionChange(direction === "asc" ? "desc" : "asc")}
      >
        <DirectionIcon size={15} />
      </button>
    </div>
  );
}

const DataRow = memo(function DataRow({
  rank,
  item,
  changeWindows,
  levelType,
  onDrill,
}: {
  rank: number;
  item: BoardRow;
  changeWindows: number[];
  levelType: LevelKey;
  onDrill: (row: BoardRow, levelType: string) => void;
}) {
  const clickable = levelType !== "CHANNEL";
  return (
    <div
      className={clickable ? "table-row clickable-row" : "table-row"}
      onClick={clickable ? () => onDrill(item, levelType) : undefined}
    >
      <span className="rank">{String(rank).padStart(2, "0")}</span>
      <span className="name" title={item.name}>{item.name}</span>
      <span className="done-cell">
        <strong>{formatNumber(item.done)}</strong>
        <em>{fmtProgress(pct(item))}</em>
      </span>
      <span className="target-cell">
        {item.target == null ? "--" : formatNumber(item.target)}
      </span>
      {changeWindows.map((minutes) => (
        <ChangeCell key={minutes} change={item.changes[minutes] ?? { value: null, rate: null }} />
      ))}
    </div>
  );
});

function EmptyRow({ message = "暂无数据" }: { message?: string }) {
  return (
    <div className="table-row" style={{ justifyContent: "center", color: "rgba(148,163,184,0.5)", padding: "20px 0" }}>
      {message}
    </div>
  );
}

function ChangeCell({ change }: { change: Change }) {
  const v = change.value; const r = change.rate;
  if ((v === null || v === 0) && (r === null || r === 0)) return <span className="change flat"><b>--</b><i>--</i></span>;
  const cls = v != null && v > 0 ? "up" : v != null && v < 0 ? "down" : "flat";
  return <span className={`change ${cls}`}><b>{v != null ? signed(v) : "--"}</b><i>{r != null ? signedPct(r * 100) : "--"}</i></span>;
}

function StatusItem({ icon: Icon, label, value, ok }: { icon: typeof Activity; label: string; value: string; ok?: boolean }) {
  return (
    <div className="status-item"><Icon size={16} /><span>{label}</span><strong className={ok ? "ok" : ""}>{value}</strong></div>
  );
}
