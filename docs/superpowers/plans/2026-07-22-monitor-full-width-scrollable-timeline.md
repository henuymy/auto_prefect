# 运行监控中心全宽与可滚动时间线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让运行监控中心在桌面端全宽布局，并让长时间线在左侧面板内独立纵向滚动。

**Architecture:** `MonitorCenter` 直接将完整筛选结果按日期分组，不再截断为首批 10 条。桌面样式将主内容容器从 1600px 最大宽度改为全宽，并把时间线面板拆成固定标题与可滚动的分组列表；移动端维持页面自然滚动。

**Tech Stack:** React 19、TypeScript、CSS、Vitest、Testing Library。

## Global Constraints

- 不修改监控 API、WebSocket、Prefect 数据或记录筛选语义。
- 不新增日期选择器、无限滚动或虚拟列表。
- 保留按日期分组、逐日展开/收起、记录详情抽屉与运行记录表格分页。
- 桌面页仍保留 `.monitor-page` 的 24px 水平内边距。
- 宽度小于 761px 时不得留下嵌套的固定高度时间线。

---

### Task 1: 覆盖完整时间线与可访问滚动区域

**Files:**
- Modify: `frontend/src/monitor/MonitorCenter.test.tsx`
- Modify: `frontend/src/monitor/MonitorCenter.tsx`

**Interfaces:**
- Consumes: `MonitorSnapshot.runs` 和既有 `MonitorService`。
- Produces: `aria-label="运行时间线记录，可向下滚动查看全部记录"` 的完整分组时间线。

- [x] **Step 1: 写入失败的交互测试**

```tsx
it("renders every filtered timeline record inside its scrollable list", async () => {
  const snapshot = await getMonitorSnapshot();
  const runs = Array.from({ length: 12 }, (_, index) => ({
    ...snapshot.runs[1],
    id: `timeline-${index}`,
    target: `通报 · 时间线记录 ${index + 1}`,
    scheduledAt: `2026-07-17T${String(23 - index).padStart(2, "0")}:00:00+08:00`,
    startedAt: `2026-07-17T${String(23 - index).padStart(2, "0")}:00:00+08:00`,
  }));
  render(<MonitorCenter service={{ getSnapshot: vi.fn().mockResolvedValue({ ...snapshot, runs, updatedAt: "2026-07-17T23:59:00+08:00" }), createStream: () => () => {} }} />);
  const timeline = await screen.findByLabelText("运行时间线记录，可向下滚动查看全部记录");
  expect(timeline.querySelectorAll(".timeline-item")).toHaveLength(12);
  expect(screen.queryByRole("button", { name: /加载更多时间线/ })).toBeNull();
});
```

- [x] **Step 2: 运行测试并确认失败**

Run: `npm run test -- MonitorCenter.test.tsx`

Expected: 该测试因滚动区域标签不存在，且时间线仍只渲染 10 条而失败。

- [x] **Step 3: 直接从完整筛选结果构建时间线**

```tsx
const groupedTimeline = useMemo(() => filteredRuns.reduce<Record<string, MonitorRun[]>>((groups, run) => {
  const key = dayLabel(referenceTime(run));
  (groups[key] ??= []).push(run);
  return groups;
}, {}), [filteredRuns]);

// Remove HISTORY_BATCH_SIZE, visibleTimelineCount, timelineRemaining, and the load-more button.
<div className="timeline-groups" tabIndex={0} aria-label="运行时间线记录，可向下滚动查看全部记录">
  {/* existing grouped timeline markup */}
</div>
```

- [x] **Step 4: 重新运行测试并确认通过**

Run: `npm run test -- MonitorCenter.test.tsx`

Expected: 所有 `MonitorCenter` 测试通过。

### Task 2: 建立全宽与桌面时间线滚动样式

**Files:**
- Modify: `frontend/src/monitor/monitorResponsive.test.ts`
- Modify: `frontend/src/monitor/monitor.css`

**Interfaces:**
- Consumes: `.monitor-page`、`.monitor-header`、`.workbar`、`.monitor-grid`、`.timeline-panel`、`.timeline-groups` CSS 类。
- Produces: 桌面全宽页面与内部可滚动时间线；移动端自然高度时间线。

- [x] **Step 1: 写入失败的 CSS 契约测试**

```ts
it("uses full-width desktop content and a separately scrollable timeline", () => {
  expect(styles).toContain(".monitor-header { margin: 0 0 20px;");
  expect(styles).toContain(".timeline-groups { flex: 1 1 auto; min-height: 0; overflow-y: auto;");
  expect(styles).toContain(".timeline-panel { display: flex; flex-direction: column;");
  expect(styles).toMatch(/\.timeline-panel \{[^}]*min-height: auto;[^}]*max-height: none;/);
  expect(styles).not.toMatch(/\.monitor-grid \{[^}]*max-width: 1600px/);
});
```

- [x] **Step 2: 运行测试并确认失败**

Run: `npm run test -- monitorResponsive.test.ts`

Expected: 新增断言因当前样式仍使用 1600px 最大宽度且时间线没有内部滚动而失败。

- [x] **Step 3: 实现全宽和独立滚动样式**

```css
.monitor-header { margin: 0 0 20px; }
.workbar { margin: 0 0 12px; }
.monitor-grid { margin: 0; }
.load-state { margin: 0; }

@media (min-width: 761px) {
  .timeline-panel { display: flex; flex-direction: column; min-height: 0; height: clamp(720px, calc(100vh - 210px), 900px); }
  .timeline-groups { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; }
}

@media (max-width: 760px) {
  .timeline-panel { min-height: auto; max-height: none; }
  .timeline-groups { overflow: visible; }
}
```

Remove only `max-width: 1600px` declarations belonging to monitor page containers.

- [x] **Step 4: 重新运行样式测试并确认通过**

Run: `npm run test -- monitorResponsive.test.ts`

Expected: CSS 契约测试通过。

### Task 3: 执行完整验证

**Files:**
- Verify: `frontend/src/monitor/MonitorCenter.tsx`
- Verify: `frontend/src/monitor/monitor.css`

- [x] **Step 1: 运行监控页测试**

Run: `npm run test -- MonitorCenter.test.tsx monitorResponsive.test.ts`

Expected: 所有相关测试通过。

- [x] **Step 2: 运行类型检查和生产构建**

Run: `npm run typecheck && npm run build`

Expected: 两个命令均以退出码 0 完成。

- [x] **Step 3: 运行差异与格式检查**

Run: `git diff --check && git diff -- frontend/src/monitor/MonitorCenter.tsx frontend/src/monitor/monitor.css frontend/src/monitor/MonitorCenter.test.tsx frontend/src/monitor/monitorResponsive.test.ts`

Expected: 无空白错误；差异仅涉及完整时间线、全宽布局、独立滚动与回归测试。

- [x] **Step 4: 提交变更**

```bash
git add frontend/src/monitor/MonitorCenter.tsx frontend/src/monitor/monitor.css frontend/src/monitor/MonitorCenter.test.tsx frontend/src/monitor/monitorResponsive.test.ts
git commit -m "feat: make monitor timeline scrollable"
```
