# 运行监控中心 Implementation Plan
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 React + Tailwind 前端中新增一个桌面优先、可筛选、使用模拟实时数据的运行监控中心。

**Architecture:** 新增独立的 `monitor.html` 入口，页面代码按数据契约、模拟服务、视图组件拆分。服务层提供与后续 REST 快照和 WebSocket 增量替换一致的接口；界面用本地状态完成筛选、记录选择、详情抽屉、技术详情和模拟实时更新。

**Tech Stack:** React 19、TypeScript、Vite、Tailwind CSS、lucide-react、Vitest。

## Global Constraints

- 保持现有 React + Tailwind + shadcn/ui 技术栈，不创建独立项目。
- 默认显示最近 7 天全部运行记录；部署、触发方式、状态和时间范围仅用于缩小范围。
- 运行记录字段固定为运行对象、触发方式、状态、计划/开始时间、当前环节、耗时。
- 点击记录打开可复用右侧详情抽屉；不提供编排控制、Prefect UI 跳转或敏感信息。
- 模拟数据须预留 REST 与 WebSocket 替换边界，并模拟运行进度、日志与更新时间变化。
- 必须提供加载、空结果、WebSocket 断开和读取错误状态。

---

### Task 1: Define monitor contracts and filterable mock service

**Files:**
- Create: `frontend/src/monitor/types.ts`
- Create: `frontend/src/monitor/mockMonitorService.ts`
- Create: `frontend/src/monitor/mockMonitorService.test.ts`

**Interfaces:**
- Produces: `MonitorRun`, `MonitorSummary`, `MonitorFilters`, `filterRuns()` and `createMockMonitorStream()`.
- Consumes: no existing runtime API; all data is structured mock data.

- [ ] **Step 1: Write the failing tests**

```ts
it("returns every record when filters are empty", () => {
  expect(filterRuns(seedRuns, EMPTY_FILTERS)).toHaveLength(seedRuns.length);
});

it("limits a deployment to its time range and returns its count", () => {
  const result = filterRuns(seedRuns, {
    ...EMPTY_FILTERS,
    target: "report-aijia",
    startAt: "2026-07-17T00:00:00+08:00",
    endAt: "2026-07-17T23:59:59+08:00",
  });
  expect(result.map((run) => run.id)).toEqual(["report-aijia-failed"]);
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `npm run test -- mockMonitorService.test.ts`

Expected: test runner reports that `mockMonitorService` is missing.

- [ ] **Step 3: Implement the smallest typed data contract and service**

```ts
export function filterRuns(runs: MonitorRun[], filters: MonitorFilters) {
  return runs.filter((run) => {
    if (filters.target !== "all" && run.targetId !== filters.target) return false;
    if (filters.trigger !== "all" && run.trigger !== filters.trigger) return false;
    if (filters.status !== "all" && run.status !== filters.status) return false;
    const at = run.startedAt ?? run.scheduledAt;
    return (!filters.startAt || at >= filters.startAt) && (!filters.endAt || at <= filters.endAt);
  });
}
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `npm run test -- mockMonitorService.test.ts`

Expected: both filtering tests pass.

### Task 2: Build the monitor page and reusable drawer

**Files:**
- Create: `frontend/src/monitor/MonitorCenter.tsx`
- Create: `frontend/src/monitor/monitor.css`
- Create: `frontend/src/monitor-main.tsx`
- Create: `frontend/monitor.html`
- Modify: `frontend/vite.config.ts`

**Interfaces:**
- Consumes: `getMonitorSnapshot()`, `filterRuns()` and `createMockMonitorStream()` from Task 1.
- Produces: `/monitor.html` rendered monitor page with functional filters and a detail drawer.

- [ ] **Step 1: Write the failing render interaction test**

```ts
it("opens the selected record in the reusable detail drawer", async () => {
  render(<MonitorCenter service={mockService} />);
  await user.click(await screen.findByRole("row", { name: /爱家亲情网每日盯控/ }));
  expect(screen.getByRole("complementary", { name: "运行详情" })).toHaveTextContent("登录状态失效，任务未完成");
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `npm run test -- MonitorCenter.test.tsx`

Expected: test runner reports that `MonitorCenter` is missing.

- [ ] **Step 3: Implement the page**

Implement the header, unified filter-and-overview toolbar, grouped time line, project-specific record table, compact mobile layout, status/count states, and a tabbed `RunDetailDrawer` used for selected scheduled/running/failed/succeeded records.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `npm run test -- MonitorCenter.test.tsx`

Expected: drawer-selection interaction passes.

### Task 3: Validate build, browser interactions, and visual fidelity

**Files:**
- Create: `design-qa.md`

- [ ] **Step 1: Run static verification**

Run: `npm run typecheck && npm run build && npm run test`

Expected: each command exits with code 0.

- [ ] **Step 2: Start the monitor entry and inspect it in the in-app browser**

Run: `npm run dev -- --host 127.0.0.1 --port 5173`

Expected: `/monitor.html` renders with the reference desktop layout.

- [ ] **Step 3: Test primary interactions**

Verify: changing a filter changes both the table count and time line; reset returns all records; clicking a record opens the drawer; tabs and technical-detail mode change drawer content; the simulated stream updates the active run and latest-update text.

- [ ] **Step 4: Record design QA**

Compare the source image and desktop screenshot at 1440×1024. Save the visual findings, interaction checks and final result to `design-qa.md`.
