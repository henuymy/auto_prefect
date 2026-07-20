# 通报待执行队列筛选 Implementation Plan
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让运行监控中心的待执行按钮和抽屉仅展示 `targetId` 以 `report-` 开头的 Scheduled 通报计划。

**Architecture:** 保持服务层、REST 契约和实时流返回完整的 `PendingQueue`。`MonitorCenter` 使用 memoized 派生值筛选通报项目并重算总数；同一派生值驱动按钮、无障碍标签和 `PendingQueueDrawer`，因此视图不会出现计数与内容不一致。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library、Vite。

## Global Constraints

- 不改变 `PendingQueue` 类型、后端 API、Mock/API 服务或 WebSocket 消息契约。
- 通报身份只能由 `targetId.startsWith("report-")` 判定，不能匹配面向用户的 `target` 文案。
- 初次快照和每次实时更新都必须重算队列。
- 保留当前工作区全部未提交改动；本次不执行暂存或提交。

---

### Task 1: 以测试驱动通报待执行队列

**Files:**
- Modify: `frontend/src/monitor/MonitorCenter.test.tsx`
- Modify: `frontend/src/monitor/MonitorCenter.tsx`

**Interfaces:**
- Consumes: `PendingQueue` 快照中的完整 `items` 和 `total`。
- Produces: 仅包含 `targetId` 以 `report-` 开头项目的派生 `PendingQueue`，范围标签为 `通报当前 Scheduled`。

- [ ] **Step 1: 写出失败的组件交互测试**

将现有待执行抽屉测试替换为以下断言，锁定显示计数、抽屉范围和排除条件：

```tsx
it("shows only report Scheduled plans in the pending queue", async () => {
  const user = userEvent.setup();
  render(<MonitorCenter />);

  const pendingQueue = await screen.findByRole("button", {
    name: "待执行 24，通报当前 Scheduled",
  });
  await user.click(pendingQueue);

  const drawer = await screen.findByRole("complementary", { name: "待执行队列" });
  expect(within(drawer).getByText("通报当前 Scheduled")).toBeTruthy();
  expect(within(drawer).getByText("24 个计划实例")).toBeTruthy();
  expect(within(drawer).getByText(/通报 · 爱家亲情网每日盯控/)).toBeTruthy();
  expect(within(drawer).queryByText("会话维护 · session-keeper")).toBeNull();
  expect(within(drawer).queryByText("驾驶舱采集 · 实时指标")).toBeNull();
});
```

- [ ] **Step 2: 运行目标测试并确认其因仍展示完整 48 项而失败**

Run: `npm run test -- MonitorCenter.test.tsx`

Expected: FAIL，待执行按钮仍返回 `待执行 48，数据库当前 Scheduled`。

- [ ] **Step 3: 在 `MonitorCenter` 中实现最小的派生队列**

在 `historyRuns` 与其余 memoized 视图数据旁新增：

```tsx
const reportPendingQueue = useMemo<PendingQueue | null>(() => {
  if (!pendingQueue) return null;
  const items = pendingQueue.items.filter((item) => item.targetId.startsWith("report-"));
  return {
    scopeLabel: "通报当前 Scheduled",
    total: items.length,
    items,
  };
}, [pendingQueue]);
```

将待执行按钮的数量和 `aria-label` 从 `pendingQueue` 改为 `reportPendingQueue`，并将抽屉渲染条件与 `queue` 属性改为该派生值：

```tsx
<button
  className="pending-queue-button"
  aria-label={`待执行 ${reportPendingQueue?.total ?? 0}，${reportPendingQueue?.scopeLabel ?? "通报当前 Scheduled"}`}
  onClick={() => { setSelectedId(null); setSummaryStatus(null); setPendingQueueOpen(true); }}
>
  <Clock3 size={15} /><span>待执行</span><b>{reportPendingQueue?.total ?? 0}</b>
</button>

{pendingQueueOpen && reportPendingQueue && (
  <PendingQueueDrawer queue={reportPendingQueue} onClose={() => setPendingQueueOpen(false)} />
)}
```

- [ ] **Step 4: 重新运行目标测试并确认通过**

Run: `npm run test -- MonitorCenter.test.tsx`

Expected: PASS，三个 `MonitorCenter` 交互测试均通过。

### Task 2: 更新维护记录并进行交付验证

**Files:**
- Modify: `PROJECT_GUIDE.md`

**Interfaces:**
- Consumes: Task 1 的最终计数和验证命令。
- Produces: 与项目维护约定一致的变更记录。

- [ ] **Step 1: 在 `PROJECT_GUIDE.md` 的变更记录中新增本次筛选说明**

新增一条 `2026-07-19 - 通报待执行队列筛选` 记录，明确：

```markdown
- 原因：待执行队列同时展示会话维护、驾驶舱采集和通报计划，无法作为通报排程视图使用。
- 修改内容：MonitorCenter 仅从完整 PendingQueue 中派生 targetId 以 report- 开头的计划，并以该派生队列驱动按钮、无障碍标签和抽屉。
- 涉及文件：frontend/src/monitor/MonitorCenter.tsx、frontend/src/monitor/MonitorCenter.test.tsx、PROJECT_GUIDE.md。
- 配置或迁移：无；API 和实时流继续传输完整待执行队列。
- 验证：运行 MonitorCenter 聚焦测试、前端类型检查、生产构建和浏览器交互检查。
- 风险与回滚：筛选依赖 targetId 的 report- 命名契约；如需恢复完整运维队列，回退 MonitorCenter 的派生队列改动即可。
```

- [ ] **Step 2: 运行完整前端自动化验证**

Run: `npm run test && npm run typecheck && npm run build`

Expected: 三个命令均以退出码 0 完成。

- [ ] **Step 3: 用浏览器验证已运行的监控页**

打开 `http://127.0.0.1:5175/monitor.html`，等待加载完成后确认待执行按钮显示 `24`；点击按钮，确认抽屉显示 `通报当前 Scheduled`、`24 个计划实例`，且列表没有“会话维护”或“驾驶舱采集”。

- [ ] **Step 4: 检查变更范围与文本完整性**

Run: `git diff --check -- frontend/src/monitor/MonitorCenter.tsx frontend/src/monitor/MonitorCenter.test.tsx PROJECT_GUIDE.md docs/superpowers/plans/2026-07-19-monitor-pending-reports.md`

Expected: 无空白错误；仅计划所列文件发生本次相关变动。
