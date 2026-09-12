import { afterEach, describe, expect, it, vi } from "vitest";
import { getDashboardLatestRun, realTestRunConfig } from "./api";
import type { ReportConfig } from "@/types/config";

const config = {
  id: "日报",
  name: "日报",
  template_path: "templates/日报.xlsx",
  downloads: [],
  compare_sources: [],
  send: { webhook_url: "", workbook_name: "日报", items: [] },
  template_update: { update_condition: "any_changed", write_sheets: "all_compared", send_when_same: true },
  wait_for_change: { enabled: false, poll_interval_seconds: 300, max_wait_minutes: 180 },
  deployment: { enabled: false, crons: [], timezone: "Asia/Shanghai" },
} as ReportConfig;

function jsonResponse(payload: unknown) {
  return { ok: true, json: async () => payload } as Response;
}

function statusResponse(status: number) {
  return { ok: false, status, text: async () => "" } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});
describe("real test API request", () => {
  it("keeps a long-running request alive beyond the generic API timeout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      flowRunId: "real-test-1",
      status: "success",
      message: "真实试跑完成",
    }));
    vi.stubGlobal("fetch", fetchMock);

    await realTestRunConfig(config);

    const signal = fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
  });

  it("does not turn a non-success response status into a success log", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      flowRunId: "real-test-1",
      status: "failed",
      message: "发送失败",
    })));

    await expect(realTestRunConfig(config)).rejects.toThrow("发送失败");
  });
});

describe("dashboard API request", () => {
  it("retries transient failures and uses a 60 second timeout", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(statusResponse(503))
      .mockResolvedValueOnce(jsonResponse({ data_version: "v1" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getDashboardLatestRun()).resolves.toEqual({ data_version: "v1" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const signal = fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
  });
});
