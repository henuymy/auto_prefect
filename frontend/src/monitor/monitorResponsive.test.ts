// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./monitor.css", import.meta.url), "utf8");

describe("monitor responsive table styles", () => {
  it("hides the horizontal-scroll hint until the mobile breakpoint", () => {
    expect(styles).toContain(".table-scroll-hint { display: none; }");
    expect(styles).toContain(".table-scroll-hint { display: flex;");
  });

  it("uses full-width desktop content and a separately scrollable timeline", () => {
    expect(styles).toContain(".monitor-header { margin: 0 0 20px;");
    expect(styles).toContain(".timeline-groups { flex: 1 1 auto; min-height: 0; overflow-y: auto;");
    expect(styles).toContain(".timeline-panel { display: flex; flex-direction: column;");
    expect(styles).toMatch(/\.timeline-panel \{[^}]*min-height: auto;[^}]*max-height: none;/);
    expect(styles).not.toMatch(/\.monitor-grid \{[^}]*max-width: 1600px/);
  });
});
