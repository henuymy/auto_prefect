// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./monitor.css", import.meta.url), "utf8");

describe("monitor responsive table styles", () => {
  it("hides the horizontal-scroll hint until the mobile breakpoint", () => {
    expect(styles).toContain(".table-scroll-hint { display: none; }");
    expect(styles).toContain(".table-scroll-hint { display: flex;");
  });
});
