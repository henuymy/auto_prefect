// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const header = readFileSync(new URL("./Header.tsx", import.meta.url), "utf8");
const sidebar = readFileSync(new URL("./Sidebar.tsx", import.meta.url), "utf8");
const documentTitle = readFileSync(new URL("../../../index.html", import.meta.url), "utf8");

describe("configuration center branding", () => {
  it("does not expose Prefect in the application brand", () => {
    expect(header).toContain(">自动化任务配置中心</h1>");
    expect(sidebar).toContain(">配置中心</div>");
    expect(documentTitle).toContain("<title>自动化任务配置中心</title>");
    expect(header).not.toContain(">基于 Prefect 的自动化任务配置中心</h1>");
    expect(sidebar).not.toContain(">Prefect 配置中心</div>");
    expect(documentTitle).not.toContain("<title>基于 Prefect 的自动化任务配置中心</title>");
  });
});
