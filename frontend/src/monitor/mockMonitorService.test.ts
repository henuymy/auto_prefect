import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS, filterRuns, monitorRuns } from "./mockMonitorService";

describe("filterRuns", () => {
  const referenceNow = new Date("2026-07-18T12:00:00+08:00");

  it("keeps only recent non-scheduled report executions", () => {
    const expiredReport = {
      ...monitorRuns[0],
      id: "report-expired",
      targetId: "report-expired",
      target: "通报 · 已过期日报",
      startedAt: "2026-06-17T09:00:00+08:00",
      scheduledAt: "2026-06-17T09:00:00+08:00",
    };

    expect(filterRuns([...monitorRuns, expiredReport], EMPTY_FILTERS, referenceNow).map((run) => run.id)).toEqual([
      "report-aijia-failed",
      "report-pksai-running",
      "report-pksai-success",
    ]);
  });

  it("limits a selected deployment to its date range", () => {
    const filtered = filterRuns(monitorRuns, {
      ...EMPTY_FILTERS,
      target: "report-aijia",
      startAt: "2026-07-17T00:00:00+08:00",
      endAt: "2026-07-17T23:59:59+08:00",
    }, referenceNow);

    expect(filtered.map((run) => run.id)).toEqual(["report-aijia-failed"]);
  });
});
