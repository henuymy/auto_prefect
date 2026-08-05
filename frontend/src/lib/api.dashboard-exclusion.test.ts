import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createDashboardChannelIndicatorExclusion,
  deleteDashboardChannelIndicatorExclusion,
  getDashboardChannelIndicatorExclusions,
  previewDashboardChannelIndicatorExclusion,
  updateDashboardChannelIndicatorExclusion,
} from "./api";

const rulePayload = {
  channel_node_id: 101,
  indicator_id: 201,
  effective_from: "2026-08-05",
  effective_to: null,
  reason: "业务口径排除",
};

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    json: async () => payload,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("dashboard channel-indicator exclusion API", () => {
  it("uses the rule endpoints and payload contract", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ exclusions: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: 1, ...rulePayload, status: "ACTIVE" }))
      .mockResolvedValueOnce(jsonResponse({ id: 1, ...rulePayload, status: "ACTIVE" }))
      .mockResolvedValueOnce(jsonResponse({
        rule: rulePayload,
        affected_nodes: [],
        affected_node_count: 0,
      }))
      .mockResolvedValueOnce(jsonResponse({ id: 1, deleted: true, status: "CANCELLED" }));
    vi.stubGlobal("fetch", fetchMock);

    await getDashboardChannelIndicatorExclusions();
    await createDashboardChannelIndicatorExclusion(rulePayload);
    await updateDashboardChannelIndicatorExclusion(1, { reason: "更新原因" });
    await previewDashboardChannelIndicatorExclusion(rulePayload);
    await deleteDashboardChannelIndicatorExclusion(1);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/dashboard/channel-indicator-exclusions",
      expect.objectContaining({
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/dashboard/channel-indicator-exclusions",
      expect.objectContaining({ method: "POST", body: JSON.stringify(rulePayload) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/dashboard/channel-indicator-exclusions/1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ reason: "更新原因" }) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/api/dashboard/channel-indicator-exclusions/preview",
      expect.objectContaining({ method: "POST", body: JSON.stringify(rulePayload) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "/api/dashboard/channel-indicator-exclusions/1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
