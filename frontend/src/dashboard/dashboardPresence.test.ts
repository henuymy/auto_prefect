// @vitest-environment node

import { describe, expect, it, vi } from "vitest";
import {
  DASHBOARD_PRESENCE_ID_STORAGE_KEY,
  getOrCreateDashboardPresenceId,
  type DashboardPresenceStorage,
} from "./dashboardPresence";

function storage(initial: string | null = null): DashboardPresenceStorage & { value: string | null } {
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

describe("dashboard presence identifier", () => {
  it("reuses a valid browser identifier", () => {
    const target = storage("connection-alpha-0001");

    expect(getOrCreateDashboardPresenceId(target)).toBe("connection-alpha-0001");
    expect(target.setItem).not.toHaveBeenCalled();
  });

  it("replaces an invalid identifier and persists the replacement", () => {
    const target = storage("invalid id");

    expect(getOrCreateDashboardPresenceId(target, () => "connection-bravo-0002")).toBe(
      "connection-bravo-0002",
    );
    expect(target.setItem).toHaveBeenCalledWith(
      DASHBOARD_PRESENCE_ID_STORAGE_KEY,
      "connection-bravo-0002",
    );
  });

  it("does not report a connection when browser storage is unavailable", () => {
    expect(getOrCreateDashboardPresenceId(null)).toBeNull();
    expect(getOrCreateDashboardPresenceId(storage(), () => "short")).toBeNull();
  });
});
