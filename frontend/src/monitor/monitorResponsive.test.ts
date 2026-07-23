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

  it("uses the record table to size the desktop row and scrolls timeline overflow internally", () => {
    expect(styles).toMatch(/\.timeline-panel \{[^}]*min-height: 720px;[^}]*height: auto;[^}]*align-self: stretch;[^}]*contain: size;[^}]*overflow: hidden;/);
    expect(styles).toMatch(/\.monitor-grid\.has-detail \.monitor-detail \{[^}]*align-self: stretch;/);
    expect(styles).toMatch(/@media \(min-width: 1321px\) \{[^}]*\.monitor-grid\.has-detail \.monitor-detail \{[^}]*contain: size;[^}]*overflow-y: auto;/);
    expect(styles).not.toContain("height: clamp(720px, calc(100vh - 210px), 900px)");
  });

  it("stacks and wraps upstream status before the narrow mobile breakpoint", () => {
    const breakpointStart = styles.indexOf("@media (max-width: 1320px) {");
    const breakpointEnd = styles.indexOf("@media", breakpointStart + 1);
    const breakpointStyles = styles.slice(breakpointStart, breakpointEnd);

    expect(breakpointStyles).toContain(".monitor-header { flex-direction: column; align-items: flex-start;");
    expect(breakpointStyles).toContain(".header-actions { width: 100%; gap: 11px 16px; flex-wrap: wrap; white-space: normal;");
  });

  it("uses a fixed dismissible notification instead of placing errors in the header", () => {
    expect(styles).toContain(".upstream-error-toast { position: fixed;");
    expect(styles).toContain(".upstream-error-dismiss { display: grid;");
    expect(styles).not.toContain(".upstream-error { flex:");
  });
});
