import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { DashboardTrendResponse } from "@/types/dashboard";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

// ── Deep Sea Pulse 色板 ──
const COLORS = {
  line: "#4fd1c5",
  area: ["rgba(79, 209, 197, 0.25)", "rgba(79, 209, 197, 0)"],
  gridLine: "rgba(255, 255, 255, 0.06)",
  text: "#94a3b8",
  textDim: "#475569",
  tooltipBg: "rgba(15, 23, 42, 0.95)",
  tooltipBorder: "rgba(79, 209, 197, 0.3)",
};

interface TrendChartProps {
  data: DashboardTrendResponse | null;
  loading?: boolean;
}

export function TrendChart({ data, loading }: TrendChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  // Init / dispose
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current, undefined, {
      renderer: "canvas",
    });
    chartRef.current = chart;

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // Build option
  const option = useMemo(() => {
    if (!data?.points?.length) return null;
    const times = data.points.map((p) => {
      const d = new Date(p.collected_at);
      return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    });
    const values = data.points.map((p) => p.value);

    return {
      backgroundColor: "transparent",
      grid: {
        top: 12,
        right: 12,
        bottom: 28,
        left: 48,
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: COLORS.tooltipBg,
        borderColor: COLORS.tooltipBorder,
        borderWidth: 1,
        textStyle: { color: "#e2e8f0", fontSize: 12 },
        formatter(params: { name: string; value: number | null }[]) {
          if (!params.length) return "";
          const p = params[0];
          const full = data.points.find((pt) => {
            const d = new Date(pt.collected_at);
            const t = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
            return t === p.name;
          });
          const dateStr = full
            ? new Date(full.collected_at).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
              })
            : p.name;
          const val =
            p.value != null ? p.value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "--";
          return `<div style="font-size:12px">${dateStr}</div><div style="font-size:16px;font-weight:700;margin-top:2px">${val}</div>`;
        },
      },
      xAxis: {
        type: "category",
        data: times,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: COLORS.textDim,
          fontSize: 10,
          interval: "auto",
          hideOverlap: true,
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: COLORS.gridLine } },
        axisLabel: { color: COLORS.textDim, fontSize: 10 },
      },
      series: [
        {
          type: "line",
          data: values,
          smooth: true,
          symbol: "circle",
          symbolSize: 4,
          showSymbol: values.length < 60,
          lineStyle: { color: COLORS.line, width: 2 },
          itemStyle: { color: COLORS.line },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: COLORS.area[0] },
              { offset: 1, color: COLORS.area[1] },
            ]),
          },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: "rgba(79, 209, 197, 0.15)", type: "dashed" },
            data: [{ type: "average" }],
            label: { show: false },
          },
        },
      ],
    } satisfies echarts.EChartsCoreOption;
  }, [data]);

  // Apply option
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (option) {
      chart.setOption(option, { notMerge: true });
    } else {
      chart.clear();
    }
  }, [option]);

  // Empty state
  if (!data?.points?.length) {
    return (
      <div className="flex h-28 items-center justify-center rounded-xl border border-dashed border-white/10 bg-black/10">
        <div className="text-center">
          <div className="text-sm font-bold text-slate-500">
            {loading ? "加载趋势数据…" : "暂无趋势数据"}
          </div>
          <div className="mt-1 text-xs text-slate-600">
            {loading ? "" : "等待更多采集批次后展示"}
          </div>
        </div>
      </div>
    );
  }

  return <div ref={containerRef} className="h-44 w-full" />;
}
