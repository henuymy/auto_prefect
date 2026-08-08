export const DASHBOARD_PRESENCE_ID_STORAGE_KEY = "dashboard-v2-presence-id";

export interface DashboardPresenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

type ConnectionIdFactory = () => string;

const CONNECTION_ID_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

function browserStorage(): DashboardPresenceStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function createConnectionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `dashboard_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
}

function isConnectionId(value: string | null): value is string {
  return Boolean(value && CONNECTION_ID_PATTERN.test(value));
}

/** Return one anonymous browser-profile identifier for the presence heartbeat. */
export function getOrCreateDashboardPresenceId(
  storage: DashboardPresenceStorage | null | undefined = browserStorage(),
  createId: ConnectionIdFactory = createConnectionId,
): string | null {
  if (!storage) return null;
  try {
    const existing = storage.getItem(DASHBOARD_PRESENCE_ID_STORAGE_KEY);
    if (isConnectionId(existing)) return existing;

    const next = createId();
    if (!isConnectionId(next)) return null;
    storage.setItem(DASHBOARD_PRESENCE_ID_STORAGE_KEY, next);
    return next;
  } catch {
    return null;
  }
}
