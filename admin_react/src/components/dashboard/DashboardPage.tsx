import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  BarChart3,
  ChevronRight,
  Clock3,
  Database,
  MapPinned,
  RefreshCw,
  Search,
  Server,
  Trophy,
} from "lucide-react";
import { getCurrentDashboard } from "@/lib/api";
import type {
  DashboardCurrentResponse,
  DashboardIndicator,
  DashboardRow,
} from "@/types/dashboard";

const LEVEL_LABELS: Record<DashboardRow["level_type"], string> = {
  CITY: "地市",
  BRANCH: "分公司",
  GRID: "网格",
  CHANNEL: "渠道",
};

function metricValue(row: DashboardRow | undefined, code: string | undefined) {
  return code && row ? row.metrics[code] ?? null : null;
}

function formatNumber(value: number | null | undefined) {
  if (value === null || value === undefined) return "--";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function formatTime(value: string | null | undefined) {
  if (!value) return "尚无成功批次";
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function levelDescription(level: DashboardRow["level_type"]) {
  const next = {
    CITY: "选择分公司继续查看",
    BRANCH: "选择网格继续查看",
    GRID: "选择渠道查看明细",
    CHANNEL: "当前已到渠道层级",
  } as const;
  return next[level];
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardCurrentResponse | null>(null);
  const [selectedAreaId, setSelectedAreaId] = useState<number | null>(null);
  const [indicatorCode, setIndicatorCode] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setError("");
      const result = await getCurrentDashboard();
      setData(result);
      setIndicatorCode((current) =>
        result.indicators.some((item) => item.code === current)
          ? current
          : result.indicators[0]?.code || "",
      );
      setSelectedAreaId((current) => {
        if (current && result.rows.some((row) => row.area_id === current)) {
          return current;
        }
        return result.rows.find((row) => row.level_type === "CITY")?.area_id ?? null;
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [selectedAreaId, indicatorCode]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const rowById = useMemo(
    () => new Map((data?.rows || []).map((row) => [row.area_id, row])),
    [data],
  );
  const selectedArea = selectedAreaId ? rowById.get(selectedAreaId) : undefined;
  const selectedIndicator = data?.indicators.find(
    (item) => item.code === indicatorCode,
  );
  const children = useMemo(
    () =>
      selectedArea
        ? (data?.rows || []).filter((row) => row.parent_id === selectedArea.area_id)
        : [],
    [data, selectedArea],
  );
  const visibleChildren = useMemo(() => {
    const normalized = keyword.trim().toLowerCase();
    if (!normalized) return children;
    return children.filter(
      (row) =>
        row.area_name.toLowerCase().includes(normalized) ||
        row.area_code.toLowerCase().includes(normalized),
    );
  }, [children, keyword]);
  const rankedChildren = useMemo(
    () =>
      [...children]
        .filter((row) => metricValue(row, indicatorCode) !== null)
        .sort(
          (left, right) =>
            (metricValue(right, indicatorCode) || 0) -
            (metricValue(left, indicatorCode) || 0),
        ),
    [children, indicatorCode],
  );
  const breadcrumbs = useMemo(() => {
    const result: DashboardRow[] = [];
    let current = selectedArea;
    while (current) {
      result.unshift(current);
      current = current.parent_id ? rowById.get(current.parent_id) : undefined;
    }
    return result;
  }, [rowById, selectedArea]);
  const childValues = rankedChildren
    .map((row) => metricValue(row, indicatorCode))
    .filter((value): value is number => value !== null);
  const maxValue = childValues.length ? Math.max(...childValues) : null;
  const minValue = childValues.length ? Math.min(...childValues) : null;
  const averageValue = childValues.length
    ? childValues.reduce((sum, value) => sum + value, 0) / childValues.length
    : null;
  const coverage = children.length
    ? Math.round((childValues.length / children.length) * 100)
    : metricValue(selectedArea, indicatorCode) === null
      ? 0
      : 100;

  return (
    <div className="dashboard-shell min-h-dvh text-slate-100">
      <div className="mx-auto max-w-[1920px] px-4 py-4 lg:px-7 lg:py-6">
        <header className="mb-5 flex flex-col gap-5 border-b border-white/10 pb-5 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-4">
            <div className="dashboard-logo">
              <BarChart3 className="h-7 w-7" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-black tracking-tight lg:text-3xl">
                  数据驾驶舱
                </h1>
                <span className="dashboard-status">
                  <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_12px_#34d399]" />
                  {data?.latest_run ? "数据服务正常" : "等待首批数据"}
                </span>
              </div>
              <p className="mt-1 text-sm text-slate-400">
                郑州市组织指标监测中心 · 独立 MySQL 数据源
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <HeaderMeta
              icon={Clock3}
              label="最近更新"
              value={formatTime(data?.latest_run?.finished_at)}
            />
            <button className="dashboard-button" onClick={() => void refresh()}>
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
            <button
              className="dashboard-button"
              onClick={() => {
                window.location.href = "/";
              }}
            >
              <ArrowLeft className="h-4 w-4" />
              配置中心
            </button>
          </div>
        </header>

        {error && (
          <div className="mb-5 rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            数据读取失败：{error}
          </div>
        )}

        <section className="dashboard-panel mb-5 flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500">
              当前查看范围
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {breadcrumbs.map((row, index) => (
                <div key={row.area_id} className="flex items-center gap-2">
                  {index > 0 && <ChevronRight className="h-4 w-4 text-slate-600" />}
                  <button
                    className={`rounded-lg px-3 py-1.5 text-sm font-bold transition ${
                      row.area_id === selectedArea?.area_id
                        ? "bg-cyan-400 text-slate-950"
                        : "bg-white/5 text-slate-300 hover:bg-white/10"
                    }`}
                    onClick={() => {
                      setSelectedAreaId(row.area_id);
                      setKeyword("");
                    }}
                  >
                    {row.area_name}
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <span className="text-xs font-bold text-slate-500">指标</span>
            <div className="flex max-w-full gap-2 overflow-x-auto pb-1">
              {data?.indicators.map((indicator) => (
                <IndicatorButton
                  key={indicator.code}
                  indicator={indicator}
                  selected={indicator.code === indicatorCode}
                  onClick={() => setIndicatorCode(indicator.code)}
                />
              ))}
            </div>
          </div>
        </section>

        <section className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            icon={Activity}
            eyebrow="当前区域指标"
            value={formatNumber(metricValue(selectedArea, indicatorCode))}
            detail={selectedIndicator?.name || "尚未配置指标"}
            accent="cyan"
          />
          <MetricCard
            icon={MapPinned}
            eyebrow={`下级${children[0] ? LEVEL_LABELS[children[0].level_type] : "区域"}`}
            value={children.length.toLocaleString("zh-CN")}
            detail={selectedArea ? levelDescription(selectedArea.level_type) : "--"}
            accent="violet"
          />
          <MetricCard
            icon={Database}
            eyebrow="数据覆盖率"
            value={`${coverage}%`}
            detail={`${childValues.length}/${children.length || 1} 个区域有有效值`}
            accent="emerald"
          />
          <MetricCard
            icon={Server}
            eyebrow="采集批次"
            value={data?.latest_run?.stat_date || "--"}
            detail={data?.latest_run?.batch_no || "尚无成功批次"}
            accent="amber"
          />
        </section>

        <section className="mb-5 grid gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(360px,0.75fr)]">
          <div className="dashboard-panel min-w-0 p-5">
            <PanelTitle
              icon={Trophy}
              title={`${children[0] ? LEVEL_LABELS[children[0].level_type] : "区域"}排名`}
              description={`${selectedArea?.area_name || "郑州市"} · ${selectedIndicator?.name || "当前指标"}`}
            />
            <div className="mt-6 grid gap-x-7 gap-y-5 md:grid-cols-2">
              {rankedChildren.slice(0, 10).map((row, index) => {
                const value = metricValue(row, indicatorCode) || 0;
                const width = maxValue ? Math.max(3, (value / maxValue) * 100) : 0;
                return (
                  <button
                    key={row.area_id}
                    className="group text-left"
                    onClick={() => {
                      setSelectedAreaId(row.area_id);
                      setKeyword("");
                    }}
                  >
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span
                          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-black ${
                            index < 3
                              ? "bg-cyan-400 text-slate-950"
                              : "bg-white/5 text-slate-400"
                          }`}
                        >
                          {index + 1}
                        </span>
                        <span className="truncate text-sm font-bold text-slate-200 group-hover:text-cyan-300">
                          {row.area_name}
                        </span>
                      </div>
                      <span className="font-mono text-sm font-black text-white">
                        {formatNumber(value)}
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-400 transition-all duration-500"
                        style={{ width: `${width}%` }}
                      />
                    </div>
                  </button>
                );
              })}
            </div>
            {!rankedChildren.length && <EmptyState text="当前层级暂无可排名数据" />}
          </div>

          <div className="dashboard-panel p-5">
            <PanelTitle
              icon={Activity}
              title="层级概览"
              description="当前下级区域统计"
            />
            <div className="mt-5 grid grid-cols-3 gap-3">
              <CompactMetric label="最高" value={formatNumber(maxValue)} />
              <CompactMetric label="平均" value={formatNumber(averageValue)} />
              <CompactMetric label="最低" value={formatNumber(minValue)} />
            </div>
          </div>
        </section>

        <section className="dashboard-panel overflow-hidden">
          <div className="flex flex-col gap-4 border-b border-white/10 p-5 lg:flex-row lg:items-center lg:justify-between">
            <PanelTitle
              icon={MapPinned}
              title={`${selectedArea?.area_name || "郑州市"}区域明细`}
              description={`${visibleChildren.length} 条结果 · 点击区域名称继续下钻`}
            />
            <label className="flex h-10 min-w-64 items-center gap-2 rounded-xl border border-white/10 bg-black/20 px-3 text-slate-300">
              <Search className="h-4 w-4 text-slate-500" />
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索名称或编码"
                className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-600"
              />
            </label>
          </div>

          <div className="max-h-[560px] overflow-auto">
            <table className="w-full min-w-[840px] text-sm">
              <thead className="sticky top-0 z-10 bg-[#111d2d]/95 text-left text-[11px] uppercase tracking-wider text-slate-500 backdrop-blur">
                <tr>
                  <th className="px-5 py-4">区域名称</th>
                  <th className="px-5 py-4">层级</th>
                  <th className="px-5 py-4">区域编码</th>
                  <th className="px-5 py-4 text-right">{selectedIndicator?.name || "指标值"}</th>
                  <th className="px-5 py-4">采集时间</th>
                  <th className="px-5 py-4 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleChildren.map((row, index) => {
                  const hasChildren = (data?.rows || []).some(
                    (item) => item.parent_id === row.area_id,
                  );
                  return (
                    <tr
                      key={row.area_id}
                      className="border-t border-white/[0.06] transition hover:bg-cyan-400/[0.04]"
                    >
                      <td className="px-5 py-4">
                        <button
                          className="flex items-center gap-3 text-left font-bold text-slate-100 hover:text-cyan-300"
                          onClick={() => {
                            if (hasChildren) {
                              setSelectedAreaId(row.area_id);
                              setKeyword("");
                            }
                          }}
                        >
                          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-white/5 text-xs text-slate-500">
                            {index + 1}
                          </span>
                          {row.area_name}
                        </button>
                      </td>
                      <td className="px-5 py-4">
                        <span className="rounded-md bg-white/5 px-2 py-1 text-xs font-bold text-slate-400">
                          {LEVEL_LABELS[row.level_type]}
                        </span>
                      </td>
                      <td className="px-5 py-4 font-mono text-xs text-slate-500">
                        {row.area_code}
                      </td>
                      <td className="px-5 py-4 text-right font-mono text-base font-black text-white">
                        {formatNumber(metricValue(row, indicatorCode))}
                      </td>
                      <td className="px-5 py-4 text-xs text-slate-500">
                        {formatTime(row.collected_at)}
                      </td>
                      <td className="px-5 py-4 text-right">
                        {hasChildren ? (
                          <button
                            className="inline-flex items-center gap-1 text-xs font-bold text-cyan-400 hover:text-cyan-300"
                            onClick={() => {
                              setSelectedAreaId(row.area_id);
                              setKeyword("");
                            }}
                          >
                            查看下级 <ChevronRight className="h-4 w-4" />
                          </button>
                        ) : (
                          <span className="text-xs text-slate-600">末级区域</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!visibleChildren.length && (
              <EmptyState
                text={
                  selectedArea?.level_type === "CHANNEL"
                    ? "当前已到渠道层级，请通过上方路径返回"
                    : "没有匹配的下级区域"
                }
              />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function HeaderMeta({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Clock3;
  label: string;
  value: string;
}) {
  return (
    <div className="hidden items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2 lg:flex">
      <Icon className="h-4 w-4 text-cyan-400" />
      <div>
        <div className="text-[10px] font-bold uppercase tracking-wider text-slate-600">{label}</div>
        <div className="text-xs font-bold text-slate-300">{value}</div>
      </div>
    </div>
  );
}

function IndicatorButton({
  indicator,
  selected,
  onClick,
}: {
  indicator: DashboardIndicator;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`whitespace-nowrap rounded-lg px-3 py-2 text-xs font-bold transition ${
        selected
          ? "bg-cyan-400 text-slate-950"
          : "border border-white/10 bg-white/[0.03] text-slate-400 hover:bg-white/[0.07]"
      }`}
    >
      {indicator.name}
    </button>
  );
}

function MetricCard({
  icon: Icon,
  eyebrow,
  value,
  detail,
  accent,
}: {
  icon: typeof Activity;
  eyebrow: string;
  value: string;
  detail: string;
  accent: "cyan" | "violet" | "emerald" | "amber";
}) {
  const accents = {
    cyan: "from-cyan-400/20 text-cyan-300",
    violet: "from-violet-400/20 text-violet-300",
    emerald: "from-emerald-400/20 text-emerald-300",
    amber: "from-amber-400/20 text-amber-300",
  };
  return (
    <div className="dashboard-panel relative overflow-hidden p-5">
      <div className={`absolute inset-y-0 left-0 w-24 bg-gradient-to-r ${accents[accent]} to-transparent opacity-60`} />
      <div className="relative flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-500">
            {eyebrow}
          </div>
          <div className="mt-3 truncate font-mono text-3xl font-black text-white">{value}</div>
          <div className="mt-2 truncate text-xs text-slate-500" title={detail}>{detail}</div>
        </div>
        <div className={`rounded-xl bg-white/5 p-3 ${accents[accent].split(" ").at(-1)}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  );
}

function PanelTitle({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Trophy;
  title: string;
  description: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="rounded-xl bg-cyan-400/10 p-2 text-cyan-400">
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <h2 className="font-black text-white">{title}</h2>
        <p className="mt-0.5 text-xs text-slate-500">{description}</p>
      </div>
    </div>
  );
}

function CompactMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.07] bg-black/10 p-3 text-center">
      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-600">{label}</div>
      <div className="mt-2 truncate font-mono text-lg font-black text-slate-100">{value}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex min-h-32 items-center justify-center p-8 text-center text-sm text-slate-600">
      {text}
    </div>
  );
}
