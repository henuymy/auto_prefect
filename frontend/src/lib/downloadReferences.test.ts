// @vitest-environment node

import { describe, expect, it } from "vitest";
import { syncDownloadReferences, updateDownloadWithReferences } from "@/lib/downloadReferences";
import type { ReportConfig } from "@/types/config";

function configFixture(): ReportConfig {
  return {
    id: "日报",
    name: "日报",
    template_path: "templates/日报.xlsx",
    downloads: [
      { name: "旧标识", source: "http_api", headers: {} },
      { name: "其他标识", source: "http_api", headers: {} },
    ],
    compare_sources: [
      { download_name: "旧标识", engine: "openpyxl", max_workers: 1, sheet_mappings: [] },
      { download_name: "其他标识", engine: "openpyxl", max_workers: 1, sheet_mappings: [] },
    ],
    send: { webhook_url: "", workbook_name: "日报", items: [] },
    template_update: { update_condition: "any_changed", write_sheets: "all_compared", send_when_same: true },
    wait_for_change: { enabled: false, poll_interval_seconds: 300, max_wait_minutes: 180 },
    deployment: { enabled: false, crons: [], timezone: "Asia/Shanghai" },
  };
}

describe("updateDownloadWithReferences", () => {
  it("updates compare source labels in the same state transition", () => {
    const config = configFixture();
    const next = updateDownloadWithReferences(config, 0, { ...config.downloads[0], name: " 新标识 " });

    expect(next.downloads[0].name).toBe(" 新标识 ");
    expect(next.compare_sources[0].download_name).toBe(" 新标识 ");
    expect(next.compare_sources[1].download_name).toBe("其他标识");
  });

  it("does not rewrite unrelated references", () => {
    const config = configFixture();
    const next = updateDownloadWithReferences(config, 1, { ...config.downloads[1], url: "/new" });

    expect(next.compare_sources).toEqual(config.compare_sources);
  });

  it("synchronizes renames made in the JSON editor", () => {
    const config = configFixture();
    const next = syncDownloadReferences(config, {
      ...config,
      downloads: config.downloads.map((item, index) => (
        index === 0 ? { ...item, name: " JSON 新标识 " } : item
      )),
    });

    expect(next.downloads[0].name).toBe(" JSON 新标识 ");
    expect(next.compare_sources[0].download_name).toBe(" JSON 新标识 ");
  });

  it("keeps references unchanged when JSON only reorders downloads", () => {
    const config = configFixture();
    const next = syncDownloadReferences(config, {
      ...config,
      downloads: [...config.downloads].reverse(),
    });

    expect(next.compare_sources.map((source) => source.download_name)).toEqual(["旧标识", "其他标识"]);
  });

  it("updates multiple independent JSON renames without cascading references", () => {
    const config = configFixture();
    const next = syncDownloadReferences(config, {
      ...config,
      downloads: [
        { ...config.downloads[0], name: "新版标识一" },
        { ...config.downloads[1], name: "新版标识二" },
      ],
    });

    expect(next.compare_sources.map((source) => source.download_name)).toEqual(["新版标识一", "新版标识二"]);
  });

  it("does not infer a rename when JSON removes a download", () => {
    const config = configFixture();
    const next = syncDownloadReferences(config, {
      ...config,
      downloads: [config.downloads[1]],
    });

    expect(next.compare_sources.map((source) => source.download_name)).toEqual(["旧标识", "其他标识"]);
  });
});
