import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MonitorCenter } from "./MonitorCenter";
import { createMockMonitorStream, getMonitorSnapshot, monitorRuns, pendingQueue } from "./mockMonitorService";
import type { MonitorStreamMessage } from "./types";

const mockService = { getSnapshot: getMonitorSnapshot, createStream: createMockMonitorStream };

afterEach(cleanup);

describe("MonitorCenter", () => {
  it("shows a 30-day report history, filters it, and opens the shared detail drawer", async () => {
    const user = userEvent.setup();
    render(<MonitorCenter service={mockService} />);

    await waitFor(() => expect(screen.getAllByText(/通报 · 爱家亲情网每日盯控/).some((node) => node.tagName === "STRONG")).toBe(true));
    expect(screen.getByText("自动通报 · 最近 30 天运行概览")).toBeTruthy();
    expect(screen.getByText((_, node) => node?.tagName === "P" && node.textContent === "最近 30 天 · 共 3 次")).toBeTruthy();
    expect(screen.queryByText("会话维护 · session-keeper")).toBeNull();
    expect(within(screen.getByLabelText("运行概况")).getByRole("button", { name: "待执行（全局） 24，通报当前 Scheduled" })).toBeTruthy();

    await user.selectOptions(screen.getByLabelText("运行对象"), "report-aijia");
    await waitFor(() => expect(screen.getByText((_, node) => node?.tagName === "P" && node.textContent === "已应用筛选条件 · 共 1 次")).toBeTruthy());

    const failedAijiaRow = screen.getAllByRole("row").find((row) => row.textContent?.includes("通报 · 爱家亲情网每日盯控") && row.textContent.includes("失败"));
    expect(failedAijiaRow).toBeTruthy();
    await user.click(failedAijiaRow!);
    await waitFor(() => expect(screen.getByRole("dialog", { name: "运行详情" })).toBeTruthy());
    expect(within(screen.getByLabelText("失败摘要")).getByText("登录状态失效，任务未完成")).toBeTruthy();
  });

  it("shows only report Scheduled plans in the pending queue", async () => {
    const user = userEvent.setup();
    render(<MonitorCenter service={mockService} />);

    const pendingQueue = await screen.findByRole("button", { name: "待执行（全局） 24，通报当前 Scheduled" });
    await user.click(pendingQueue);

    const drawer = await screen.findByRole("dialog", { name: "待执行队列" });
    expect(within(drawer).getByText("通报当前 Scheduled")).toBeTruthy();
    expect(within(drawer).getByText("24 个计划实例")).toBeTruthy();
    expect(within(drawer).getByText(/全局通报 Scheduled 队列，不受运行记录筛选影响。/)).toBeTruthy();
    expect(within(drawer).getAllByText(/通报 · 爱家亲情网每日盯控/).length).toBeGreaterThan(0);
    expect(within(drawer).queryByText("会话维护 · session-keeper")).toBeNull();
    expect(within(drawer).queryByText("驾驶舱采集 · 实时指标")).toBeNull();
  });

  it("opens success, running, and failed summaries in the same drawer position", async () => {
    const user = userEvent.setup();
    render(<MonitorCenter service={mockService} />);

    const success = await screen.findByRole("button", { name: "成功 1" });
    await user.click(success);
    expect(await screen.findByRole("dialog", { name: "成功运行" })).toBeTruthy();
  });

  it("uses the monitor API by default", async () => {
    const snapshot = await getMonitorSnapshot();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", class { addEventListener() {} close() {} });

    render(<MonitorCenter />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/monitor/snapshot",
      { headers: { Accept: "application/json" } },
    ));
    vi.unstubAllGlobals();
  });

  it("renders UTC monitor timestamps in China Standard Time", async () => {
    const snapshot = {
      runs: [{
        ...monitorRuns[0],
        scheduledAt: "2026-07-19T15:00:00+00:00",
        startedAt: "2026-07-19T15:01:53+00:00",
        finishedAt: "2026-07-19T15:02:13+00:00",
      }],
      pendingQueue: structuredClone(pendingQueue),
      updatedAt: "2026-07-19T15:01:53+00:00",
      connected: true,
    };
    const service = {
      getSnapshot: vi.fn().mockResolvedValue(snapshot),
      createStream: () => () => {},
    };

    const view = render(<MonitorCenter service={service} />);
    const scoped = within(view.container);

    expect(await scoped.findByText("数据快照：2026-07-19 23:01:53")).toBeTruthy();
    expect(scoped.getByRole("cell", { name: "2026-07-19 23:01:53" })).toBeTruthy();
  });

  it("merges run.updated without requesting a second snapshot", async () => {
    const snapshot = await getMonitorSnapshot();
    const getSnapshot = vi.fn().mockResolvedValue(snapshot);
    let emit: ((message: MonitorStreamMessage) => void) | undefined;
    const service = {
      getSnapshot,
      createStream(onUpdate: (message: MonitorStreamMessage) => void) {
        emit = onUpdate;
        return () => {};
      },
    };

    render(<MonitorCenter service={service} />);
    await waitFor(() => expect(emit).toBeDefined());

    emit!({
      type: "run.updated",
      runId: "run-1",
      run: { ...snapshot.runs[0], status: "succeeded" },
      pendingQueue: { scopeLabel: "通报当前 Scheduled", total: 0, items: [] },
      summary: { succeeded: 2, running: 0, failed: 1, scheduled: 0 },
      updatedAt: "2026-07-19T09:00:01+08:00",
      connected: true,
      upstream: { lastAcceptedAt: null, lastReconciledAt: null, lastErrorCategory: null },
    });

    expect(await screen.findByRole("button", { name: "成功 2" })).toBeTruthy();
    expect(getSnapshot).toHaveBeenCalledTimes(1);
  });
});
describe("MonitorCenter live state", () => {
  it("loads Prefect run details only after the user opens a record", async () => {
    const user = userEvent.setup();
    const snapshot = await getMonitorSnapshot();
    const selected = { ...snapshot.runs[0], steps: [], logs: [] };
    const getRunDetail = vi.fn().mockResolvedValue({
      ...snapshot.runs[0],
      steps: [{ name: "真实 Prefect 步骤", status: "failed", message: "步骤失败" }],
      logs: [{ at: "2026-07-17T09:03:05+08:00", level: "ERROR", message: "真实 ERROR 日志" }],
    });
    const service = {
      getSnapshot: vi.fn().mockResolvedValue({ ...snapshot, runs: [selected] }),
      getRunDetail,
      createStream: () => () => {},
    };

    render(<MonitorCenter service={service} />);
    const row = await screen.findByRole("row", { name: /通报 · 爱家亲情网每日盯控.*失败/ });
    await user.click(row);

    await waitFor(() => expect(getRunDetail).toHaveBeenCalledWith(selected.id));
    expect(await screen.findByText("真实 Prefect 步骤")).toBeTruthy();
  });

  it("separates page connection from upstream event and reconciliation timestamps", async () => {
    const snapshot = await getMonitorSnapshot();
    const service = {
      getSnapshot: vi.fn().mockResolvedValue({
        ...snapshot,
        upstream: {
          lastAcceptedAt: "2026-07-19T01:00:00+00:00",
          lastReconciledAt: "2026-07-19T01:00:01+00:00",
          lastErrorCategory: "RECONCILIATION_FAILED",
        },
      }),
      createStream: () => () => {},
    };

    render(<MonitorCenter service={service} />);

    expect(await screen.findAllByText("页面实时：已连接")).toHaveLength(1);
    expect(screen.getByText("上游事件：2026-07-19 09:00:00")).toBeTruthy();
    expect(screen.getByText("最近对账：2026-07-19 09:00:01")).toBeTruthy();
    expect(screen.getByText("上游异常：RECONCILIATION_FAILED")).toBeTruthy();
  });

  it("shows relative upstream and reconciliation ages", async () => {
    const snapshot = await getMonitorSnapshot();
    const service = {
      getSnapshot: vi.fn().mockResolvedValue({
        ...snapshot,
        updatedAt: "2026-07-19T09:00:10+08:00",
        upstream: {
          lastAcceptedAt: "2026-07-19T09:00:00+08:00",
          lastReconciledAt: "2026-07-19T09:00:08+08:00",
          lastErrorCategory: null,
        },
      }),
      createStream: () => () => {},
    };

    render(<MonitorCenter service={service} />);

    expect(await screen.findByText("上游事件：2026-07-19 09:00:00（10 秒前）")).toBeTruthy();
    expect(screen.getByText("最近对账：2026-07-19 09:00:08（2 秒前）")).toBeTruthy();
  });

  it("paginates records and lets the user change the page size", async () => {
    const user = userEvent.setup();
    const snapshot = await getMonitorSnapshot();
    const runs = Array.from({ length: 25 }, (_, index) => ({
      ...snapshot.runs[0],
      id: `failed-${index}`,
      scheduledAt: `2026-07-17T09:${String(index).padStart(2, "0")}:00+08:00`,
      startedAt: `2026-07-17T09:${String(index).padStart(2, "0")}:00+08:00`,
    }));
    const service = {
      getSnapshot: vi.fn().mockResolvedValue({ ...snapshot, runs, updatedAt: "2026-07-17T10:00:00+08:00" }),
      createStream: () => () => {},
    };

    render(<MonitorCenter service={service} />);

    expect(await screen.findByLabelText("每页显示")).toBeTruthy();
    expect(screen.getByText("第 1 / 3 页")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(11);

    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("第 2 / 3 页")).toBeTruthy();

    await user.selectOptions(screen.getByLabelText("每页显示"), "20");
    expect(screen.getByText("第 1 / 2 页")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(21);
  });

  it("collapses historical timeline days while keeping today expanded by default", async () => {
    const user = userEvent.setup();
    const snapshot = await getMonitorSnapshot();
    const runs = [
      { ...snapshot.runs[1], id: "today-run", scheduledAt: "2026-07-17T09:00:00+08:00", startedAt: "2026-07-17T09:00:00+08:00" },
      { ...snapshot.runs[1], id: "history-success", scheduledAt: "2026-07-16T09:00:00+08:00", startedAt: "2026-07-16T09:00:00+08:00" },
      { ...snapshot.runs[0], id: "history-failed", scheduledAt: "2026-07-16T08:00:00+08:00", startedAt: "2026-07-16T08:00:00+08:00" },
    ];
    const service = {
      getSnapshot: vi.fn().mockResolvedValue({ ...snapshot, runs, updatedAt: "2026-07-17T12:00:00+08:00" }),
      createStream: () => () => {},
    };

    render(<MonitorCenter service={service} />);

    expect(await screen.findByRole("button", { name: "收起 2026-07-17" })).toBeTruthy();
    expect(screen.getByText("2 次 · 1 失败")).toBeTruthy();
    const historyToggle = screen.getByRole("button", { name: "展开 2026-07-16" });
    await user.click(historyToggle);
    expect(screen.getByRole("button", { name: "收起 2026-07-16" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "收起 2026-07-17" }));
    expect(screen.getByRole("button", { name: "展开 2026-07-17" })).toBeTruthy();
  });
});

describe("Monitor startup-failure fallback", () => {
  it("shows the meaningful startup step in the run table for a legacy Prefect summary", async () => {
    const snapshot = await getMonitorSnapshot();
    const failed = snapshot.runs.find((run) => run.status === "failed")!;
    const rawSummary = "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)";
    const service = {
      getSnapshot: vi.fn().mockResolvedValue({
        ...snapshot,
        runs: [{ ...failed, currentStep: "运行失败", error: { ...failed.error!, failedStep: "运行失败", businessSummary: rawSummary, technicalSummary: rawSummary } }],
      }),
      createStream: () => () => {},
    };

    render(<MonitorCenter service={service} />);

    expect(await screen.findByText("调度初始化")).toBeTruthy();
  });
});