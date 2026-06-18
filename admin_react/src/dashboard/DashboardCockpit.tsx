import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BarChart3,
  ChevronDown,
  Database,
  RefreshCw,
  Signal,
} from "lucide-react";
import {
  getDashboardOverview,
  getDashboardDrillDown,
  getDashboardWithChanges,
  getAccDashboard,
} from "@/lib/api";
import type {
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

/** Raw fetched data that doesn't change when indicator selection changes. */
type FetchedData = {
  online: boolean;
  latestRun: {
    batch_no: string;
    finished_at: string | null;
  } | null;
  updatedAt: string;
  indicators: DashboardIndicator[];
  changesRows: DashboardRowWithChanges[];
  accRows: DashboardRow[];
  levelOverrides: Partial<Record<LevelKey, LevelOverrideData>>;
};

/* ── constants ── */

const DEFAULT_CHANGE_WINDOWS = [5, 15, 30, 60];

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

function sortRows(rows: BoardRow[], sortKey: SortKey) {
  return [...rows].sort((left, right) => {
    if (sortKey === "progress") {
      return progressScore(right) - progressScore(left);
    }
    if (sortKey.startsWith("changeRate:")) {
      const m = Number(sortKey.replace("changeRate:", ""));
      return (right.changes[m].rate ?? -Infinity) - (left.changes[m].rate ?? -Infinity);
    }
    if (sortKey.startsWith("changeValue:")) {
      const m = Number(sortKey.replace("changeValue:", ""));
      return (right.changes[m].value ?? -Infinity) - (left.changes[m].value ?? -Infinity);
    }
    return right.done - left.done;
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
  changeWindows: number[],
): { dayLevels: LevelBoard[]; monthLevels: LevelBoard[] } {
  const defaultScope = defaultScopedLowerLevels
    ? buildDefaultBranchScope(changesRows)
    : null;
  const scopedChangesRows = defaultScopedLowerLevels
    ? scopeLowerLevelsToDefaultBranch(changesRows, defaultScope)
    : changesRows;
  const scopedAccRows = defaultScopedLowerLevels
    ? scopeLowerLevelsToDefaultBranch(accRows, defaultScope)
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
): T[] {
  if (!scope) return rows;

  return rows.filter((row) => {
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
  const [data, setData] = useState<FetchedData | null>(null);
  const [error, setError] = useState(false);
  const [indicator, setIndicator] = useState("");
  const [changeWindows, setChangeWindows] = useState<number[]>(DEFAULT_CHANGE_WINDOWS);
  const [drillStack, setDrillStack] = useState<DrillEntry[]>([]);
  const [scopeMode, setScopeMode] = useState<ScopeMode>("default");
  const [dayLevelAllMode, setDayLevelAllMode] = useState<Partial<Record<LevelKey, boolean>>>({});
  const [monthLevelAllMode, setMonthLevelAllMode] = useState<Partial<Record<LevelKey, boolean>>>({});
  const fetchSeqRef = useRef(0);
  const [daySorts, setDaySorts] = useState<Record<LevelKey, SortKey>>({
    BRANCH: "progress",
    GRID: "changeValue:60",
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

  useEffect(() => {
    if (!sameDrillStack(drillStack, normalizedDrillStack)) {
      setDrillStack(normalizedDrillStack);
    }
  }, [drillStack, normalizedDrillStack]);

  /* fetch — re‑runs only when drill changes */
  const fetchData = useCallback(async () => {
    const seq = fetchSeqRef.current + 1;
    fetchSeqRef.current = seq;
    setLoading(true);
    setError(false);
    try {
      const parentId = drillTarget?.areaId;
      const parentLevel = drillTarget?.levelType;
      const [changesData, accRows] = parentId == null
        ? scopeMode === "all"
          ? await Promise.all([
              getDashboardWithChanges(undefined, undefined, changeWindows),
              getAccDashboard("DAY_ACC"),
            ]).then(([changes, acc]) => [changes, acc.rows] as const)
          : await getDashboardOverview(undefined, "AQ", "DAY_ACC", changeWindows).then((overview) => [
              overview,
              overview.acc_rows,
            ] as const)
        : parentLevel
          ? await getDashboardDrillDown(parentId, parentLevel, "DAY_ACC", changeWindows).then((drill) => [
              drill,
              drill.acc_rows,
            ] as const)
        : await Promise.all([
            getDashboardWithChanges(undefined, parentId, changeWindows),
            getAccDashboard("DAY_ACC", undefined, undefined, parentId)
              .then((accData) => accData.rows)
              .catch(() => []),
          ]);

      const levelOverrides: Partial<Record<LevelKey, LevelOverrideData>> = {};
      await Promise.all(
        (["GRID", "CHANNEL"] as LevelKey[])
          .filter((level) => dayLevelAllMode[level] || monthLevelAllMode[level])
          .map(async (level) => {
            try {
              const [overrideChanges, overrideAccRows] = await Promise.all([
                getDashboardWithChanges(level, undefined, changeWindows),
                getAccDashboard("DAY_ACC", undefined, level)
                  .then((accData) => accData.rows)
                  .catch(() => []),
              ]);
              levelOverrides[level] = {
                changesRows: overrideChanges.rows,
                accRows: overrideAccRows,
              };
            } catch {
              if (seq === fetchSeqRef.current) {
                setDayLevelAllMode((prev) => ({ ...prev, [level]: false }));
                setMonthLevelAllMode((prev) => ({ ...prev, [level]: false }));
              }
            }
          }),
      );

      if (seq !== fetchSeqRef.current) return;

      const latestRun = changesData.latest_run ?? null;
      setData({
        online: Boolean(latestRun),
        latestRun,
        updatedAt: latestRun?.finished_at
          ? new Date(latestRun.finished_at).toLocaleString("zh-CN", { hour12: false })
          : nowText(),
        indicators: changesData.indicators,
        changesRows: changesData.rows,
        accRows,
        levelOverrides,
      });
    } catch {
      if (seq !== fetchSeqRef.current) return;
      setError(true);
      setData((prev) =>
        prev ? { ...prev, online: false, updatedAt: nowText() } : null,
      );
    } finally {
      if (seq === fetchSeqRef.current) setLoading(false);
    }
  }, [
    drillTarget?.areaId,
    drillTarget?.levelType,
    changeWindows,
    scopeMode,
    dayLevelAllMode.GRID,
    dayLevelAllMode.CHANNEL,
    monthLevelAllMode.GRID,
    monthLevelAllMode.CHANNEL,
  ]);

  useEffect(() => {
    void fetchData();
    const timer = window.setInterval(() => void fetchData(), 30_000);
    return () => window.clearInterval(timer);
  }, [fetchData]);

  /* auto‑select first indicator when data first arrives */
  useEffect(() => {
    if (data?.indicators.length) {
      setIndicator((prev) =>
        data.indicators.some((ind) => ind.code === prev)
          ? prev
          : data.indicators[0].code,
      );
    }
  }, [data?.indicators]);

  /* derive boards from data + indicator + visible */
  const activeCode = indicator || data?.indicators[0]?.code || "";

  const { dayLevels, monthLevels } = useMemo(() => {
    if (!data) return { dayLevels: [] as LevelBoard[], monthLevels: [] as LevelBoard[] };
    const dayOverrides = Object.fromEntries(
      (["GRID", "CHANNEL"] as LevelKey[])
        .filter((level) => dayLevelAllMode[level])
        .map((level) => [level, data.levelOverrides[level]]),
    ) as Partial<Record<LevelKey, LevelOverrideData>>;
    const monthOverrides = Object.fromEntries(
      (["GRID", "CHANNEL"] as LevelKey[])
        .filter((level) => monthLevelAllMode[level])
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
      setScopeMode("default");
      setDrillStack([]);
      return;
    }
    if (value === "__all__") {
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
    setScopeMode("default");
    setDrillStack([{
      areaId: branch.area_id,
      areaName: branch.area_name,
      levelType: branch.level_type,
    }]);
  }, [data?.changesRows]);

  const handleToggleDayLevelAll = useCallback((level: LevelKey) => {
    if (level === "BRANCH") return;
    setDayLevelAllMode((prev) => ({
      ...prev,
      [level]: !prev[level],
    }));
  }, []);

  const handleToggleMonthLevelAll = useCallback((level: LevelKey) => {
    if (level === "BRANCH") return;
    setMonthLevelAllMode((prev) => ({
      ...prev,
      [level]: !prev[level],
    }));
  }, []);

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
    const d = new Date();
    const next = Math.ceil((d.getMinutes() + 1) / 5) * 5;
    if (next >= 60) { d.setHours(d.getHours() + 1); d.setMinutes(0); }
    else d.setMinutes(next);
    d.setSeconds(0);
    return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
  }, []);

  /* ── render ── */
  return (
    <div className="cockpit-shell">
      <Header
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
        indicators={data?.indicators || []}
        onIndicatorChange={setIndicator}
        changeWindows={changeWindows}
        onChangeWindow={updateChangeWindow}
        online={data?.online ?? false}
        updatedAt={data?.updatedAt ?? nowText()}
        loading={loading}
        onRefresh={fetchData}
      />

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
            levelAllActive={Boolean(dayLevelAllMode[level.key])}
            onToggleLevelAll={level.key !== "BRANCH" ? handleToggleDayLevelAll : undefined}
            isBranch={level.key === "BRANCH"}
            branchHeight={branchHeight}
            branchPanelRef={level.key === "BRANCH" ? branchPanelRef : undefined}
          />
        ))}
      </main>

      {monthLevels.length > 0 && (
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
    </div>
  );
}

/* ── sub-components ── */

function Header({
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
}: {
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
}) {
  const indOptions = indicators.map((ind) => ({ label: ind.name || ind.code, value: ind.code }));

  return (
    <header className="cockpit-header">
      <div className="brand">
        <div className="brand-icon"><BarChart3 size={28} /></div>
        <div className="brand-title">数据驾驶舱</div>
      </div>
      <div className="toolbar">
        <Selector
          label="组织范围"
          value={scopeValue}
          displayValue={drillName}
          options={scopeOptions}
          onChange={onScopeChange}
        />
        <Selector
          label="指标"
          value={activeCode}
          options={indOptions.length ? indOptions : undefined}
          onChange={onIndicatorChange}
        />
        <WindowSelector
          windows={changeWindows}
          onChange={onChangeWindow}
        />
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
      </div>
    </header>
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
  return (
    <label className="selector">
      <span>{label}</span>
      {options ? (
        <select value={value} onChange={(e) => onChange?.(e.target.value)}>
          {displayValue && !options.some((option) => option.value === value) && (
            <option value={value}>{displayValue}</option>
          )}
          {options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      ) : (
        <strong>{displayValue || value}</strong>
      )}
      <ChevronDown size={16} />
    </label>
  );
}

function LevelPanel({
  level,
  sortKey,
  onSortChange,
  changeWindows,
  onDrill,
  levelAllActive,
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
  onToggleLevelAll?: (level: LevelKey) => void;
  isBranch?: boolean;
  branchHeight?: number;
  branchPanelRef?: React.RefObject<HTMLDivElement | null>;
}) {
  const rows = useMemo(() => sortRows(level.dayRows, sortKey), [level.dayRows, sortKey]);
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

  return (
    <section className="level-panel" ref={branchPanelRef} style={panelStyle}>
      <div className="panel-head">
        <div>
          <span className="panel-index">{level.index}</span>
          <span className="panel-title">{level.title}</span>
          <span className="panel-dot" />
          <span className="panel-count">{level.total}项</span>
        </div>
        <div className="panel-actions">
          {onToggleLevelAll && (
            <button
              className={levelAllActive ? "level-reset-button active" : "level-reset-button"}
              type="button"
              onClick={() => onToggleLevelAll(level.key)}
            >
              {levelAllActive
                ? "当前范围"
                : level.key === "GRID"
                  ? "全部网格"
                  : "全部渠道"}
            </button>
          )}
        <label className="sort-select">
          <select value={sortKey} onChange={(e) => onSortChange(e.target.value as SortKey)}>
            {sortOptions(changeWindows).map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
          <ChevronDown size={15} />
        </label>
        </div>
      </div>

      <div className="data-table" style={{ padding: "10px 10px 0", ...dataTableStyle }}>
        <div className="table-row table-head">
          <span>排名</span><span>名称</span>
          <span>完成</span>
          <span>目标</span>
          {changeWindows.map((minutes) => (
            <span key={minutes}>{minutes}分钟</span>
          ))}
        </div>
        {rows.map((r, i) => (
          <DataRow
            key={r.areaId}
            rank={i + 1}
            item={r}
            changeWindows={changeWindows}
            clickable={level.key !== "CHANNEL"}
            onClick={() => onDrill(r, level.key)}
          />
        ))}
        {rows.length === 0 && <EmptyRow />}
      </div>

      <div className="section-total">
        <span>合计</span><strong>{formatNumber(totalDone)}</strong>
        <span>平均进度</span><strong>{fmtProgress(avgPct)}</strong>
        <span>{lastWindow}分钟</span>
        <strong className={lastChange >= 0 ? "up" : "down"}>{signed(lastChange)}</strong>
      </div>
    </section>
  );
}

function DataRow({
  rank,
  item,
  changeWindows,
  clickable,
  onClick,
}: {
  rank: number;
  item: BoardRow;
  changeWindows: number[];
  clickable: boolean;
  onClick: () => void;
}) {
  return (
    <div className={clickable ? "table-row clickable-row" : "table-row"} onClick={clickable ? onClick : undefined}>
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
}

function EmptyRow() {
  return (
    <div className="table-row" style={{ justifyContent: "center", color: "rgba(148,163,184,0.5)", padding: "20px 0" }}>
      暂无数据
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
