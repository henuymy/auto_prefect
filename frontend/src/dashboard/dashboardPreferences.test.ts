// @vitest-environment node

import { describe, expect, it, vi } from "vitest";
import {
  DASHBOARD_PREFERENCES_STORAGE_KEY,
  DASHBOARD_PREFERENCES_VERSION,
  loadDashboardPreferences,
  parseDashboardPreferences,
  resolveSingleIndicatorCode,
  saveDashboardPreferences,
  updateDashboardPreferences,
  type DashboardPreferencesStorage,
} from "./dashboardPreferences";

function storage(initial: string | null = null): DashboardPreferencesStorage & {
  value: string | null;
} {
  let value = initial;
  return {
    get value() {
      return value;
    },
    set value(next: string | null) {
      value = next;
    },
    getItem: vi.fn(() => value),
    setItem: vi.fn((_key: string, next: string) => {
      value = next;
    }),
  };
}

describe("dashboard cockpit preferences", () => {
  it("uses defaults for missing, malformed, and unsupported versions", () => {
    const defaults = {
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "single",
      singleIndicatorCode: "",
      daySorts: {},
      monthSorts: {},
    } as const;

    expect(parseDashboardPreferences(null)).toEqual(defaults);
    expect(parseDashboardPreferences("not-json")).toEqual(defaults);
    expect(parseDashboardPreferences(JSON.stringify({ version: 2, mode: "multi" }))).toEqual(defaults);
  });

  it("normalizes a valid versioned payload", () => {
    expect(parseDashboardPreferences(JSON.stringify({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "multi",
      singleIndicatorCode: "  sheng-dang  ",
    }))).toEqual({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "multi",
      singleIndicatorCode: "sheng-dang",
      daySorts: {},
      monthSorts: {},
    });
    expect(parseDashboardPreferences(JSON.stringify({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "unknown",
      singleIndicatorCode: 42,
    }))).toMatchObject({ mode: "single", singleIndicatorCode: "" });
    expect(parseDashboardPreferences(JSON.stringify({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "single",
      singleIndicatorCode: "one,two",
    }))).toMatchObject({ singleIndicatorCode: "" });
    expect(parseDashboardPreferences(JSON.stringify({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "single",
      singleIndicatorCode: "x".repeat(101),
    }))).toMatchObject({ singleIndicatorCode: "" });
    expect(parseDashboardPreferences(JSON.stringify({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "single",
      singleIndicatorCode: "sheng-dang",
      daySorts: {
        BRANCH: { key: " progress ", direction: "asc" },
        GRID: { key: 42, direction: "asc" },
      },
      monthSorts: "invalid",
    }))).toMatchObject({
      daySorts: {
        BRANCH: { key: "progress", direction: "asc" },
      },
      monthSorts: {},
    });
  });

  it("reads and writes browser storage under a stable key", () => {
    const target = storage();

    saveDashboardPreferences({
      mode: "multi",
      singleIndicatorCode: "sheng-dang",
      daySorts: {
        BRANCH: { key: "progress", direction: "asc" },
      },
    }, target);

    expect(target.setItem).toHaveBeenCalledWith(
      DASHBOARD_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        version: DASHBOARD_PREFERENCES_VERSION,
        mode: "multi",
        singleIndicatorCode: "sheng-dang",
        daySorts: {
          BRANCH: { key: "progress", direction: "asc" },
        },
        monthSorts: {},
      }),
    );
    expect(loadDashboardPreferences(target)).toEqual({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "multi",
      singleIndicatorCode: "sheng-dang",
      daySorts: {
        BRANCH: { key: "progress", direction: "asc" },
      },
      monthSorts: {},
    });
  });

  it("merges a patch without dropping the other preference", () => {
    const target = storage(JSON.stringify({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "single",
      singleIndicatorCode: "sheng-dang",
      daySorts: {
        BRANCH: { key: "done", direction: "desc" },
      },
      monthSorts: {},
    }));

    updateDashboardPreferences({ mode: "multi" }, target);

    expect(loadDashboardPreferences(target)).toEqual({
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: "multi",
      singleIndicatorCode: "sheng-dang",
      daySorts: {
        BRANCH: { key: "done", direction: "desc" },
      },
      monthSorts: {},
    });
  });

  it("survives storage exceptions", () => {
    const failing: DashboardPreferencesStorage = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
    };

    expect(loadDashboardPreferences(failing)).toMatchObject({ mode: "single" });
    expect(() => saveDashboardPreferences({ mode: "single", singleIndicatorCode: "" }, failing)).not.toThrow();
  });

  it("keeps a saved code when still available and falls back otherwise", () => {
    const available = ["other", "sheng-dang", "third"] as const;

    expect(resolveSingleIndicatorCode("sheng-dang", available)).toBe("sheng-dang");
    expect(resolveSingleIndicatorCode("missing", available)).toBe("other");
    expect(resolveSingleIndicatorCode("  sheng-dang ", available)).toBe("sheng-dang");
    expect(resolveSingleIndicatorCode(null, available)).toBe("other");
    expect(resolveSingleIndicatorCode("sheng-dang", [])).toBe("");
  });
});
