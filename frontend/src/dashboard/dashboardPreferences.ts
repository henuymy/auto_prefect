/** Browser-scoped preferences for the dashboard cockpit. */

export const DASHBOARD_PREFERENCES_STORAGE_KEY = "dashboard-v2-cockpit-preferences";
export const DASHBOARD_PREFERENCES_VERSION = 1 as const;

export type DashboardCockpitMode = "single" | "multi";
export type DashboardSortDirection = "asc" | "desc";
export type DashboardSortEntry = {
  key: string;
  direction: DashboardSortDirection;
};
export type DashboardSortMap = Record<string, DashboardSortEntry>;

export type DashboardPreferences = {
  version: typeof DASHBOARD_PREFERENCES_VERSION;
  mode: DashboardCockpitMode;
  singleIndicatorCode: string;
  daySorts: DashboardSortMap;
  monthSorts: DashboardSortMap;
};

export type DashboardPreferencesValues = Pick<
  DashboardPreferences,
  "mode" | "singleIndicatorCode"
> & Partial<Pick<DashboardPreferences, "daySorts" | "monthSorts">>;

export type DashboardPreferencesPatch = Partial<DashboardPreferencesValues>;

export interface DashboardPreferencesStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const DEFAULT_DASHBOARD_PREFERENCES: DashboardPreferences = {
  version: DASHBOARD_PREFERENCES_VERSION,
  mode: "single",
  singleIndicatorCode: "",
  daySorts: {},
  monthSorts: {},
};
const MAX_INDICATOR_CODE_LENGTH = 100;

function defaultPreferences(): DashboardPreferences {
  return {
    ...DEFAULT_DASHBOARD_PREFERENCES,
    daySorts: { ...DEFAULT_DASHBOARD_PREFERENCES.daySorts },
    monthSorts: { ...DEFAULT_DASHBOARD_PREFERENCES.monthSorts },
  };
}

function browserStorage(): DashboardPreferencesStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function normalizeMode(value: unknown): DashboardCockpitMode {
  return value === "multi" ? "multi" : "single";
}

function normalizeIndicatorCode(value: unknown): string {
  if (typeof value !== "string") return "";
  const code = value.trim();
  return code.length <= MAX_INDICATOR_CODE_LENGTH && !code.includes(",") ? code : "";
}

function normalizeSortDirection(value: unknown): DashboardSortDirection {
  return value === "asc" ? "asc" : "desc";
}

function normalizeSortMap(value: unknown): DashboardSortMap {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, entry]) => (
        entry
        && typeof entry === "object"
        && !Array.isArray(entry)
        && typeof (entry as { key?: unknown }).key === "string"
      ))
      .map(([level, entry]) => {
        const raw = entry as { key: string; direction?: unknown };
        const key = raw.key.trim();
        return [level, {
          key: key.slice(0, MAX_INDICATOR_CODE_LENGTH),
          direction: normalizeSortDirection(raw.direction),
        }];
      }),
  );
}

/** Parse one persisted value. Unknown versions and malformed values use defaults. */
export function parseDashboardPreferences(raw: string | null | undefined): DashboardPreferences {
  if (!raw) return defaultPreferences();

  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      !parsed
      || typeof parsed !== "object"
      || (parsed as { version?: unknown }).version !== DASHBOARD_PREFERENCES_VERSION
    ) {
      return defaultPreferences();
    }

    const values = parsed as {
      mode?: unknown;
      singleIndicatorCode?: unknown;
      daySorts?: unknown;
      monthSorts?: unknown;
    };
    return {
      version: DASHBOARD_PREFERENCES_VERSION,
      mode: normalizeMode(values.mode),
      singleIndicatorCode: normalizeIndicatorCode(values.singleIndicatorCode),
      daySorts: normalizeSortMap(values.daySorts),
      monthSorts: normalizeSortMap(values.monthSorts),
    };
  } catch {
    return defaultPreferences();
  }
}

/** Read preferences from the supplied storage, or this browser's localStorage. */
export function loadDashboardPreferences(
  storage: DashboardPreferencesStorage | null | undefined = browserStorage(),
): DashboardPreferences {
  if (!storage) return defaultPreferences();
  try {
    return parseDashboardPreferences(storage.getItem(DASHBOARD_PREFERENCES_STORAGE_KEY));
  } catch {
    return defaultPreferences();
  }
}

/** Persist values under the current schema version. Storage failures are non-fatal. */
export function saveDashboardPreferences(
  values: DashboardPreferencesValues | null | undefined,
  storage: DashboardPreferencesStorage | null | undefined = browserStorage(),
): void {
  if (!storage) return;

  const payload: DashboardPreferences = {
    version: DASHBOARD_PREFERENCES_VERSION,
    mode: normalizeMode(values?.mode),
    singleIndicatorCode: normalizeIndicatorCode(values?.singleIndicatorCode),
    daySorts: normalizeSortMap(values?.daySorts),
    monthSorts: normalizeSortMap(values?.monthSorts),
  };

  try {
    storage.setItem(
      DASHBOARD_PREFERENCES_STORAGE_KEY,
      JSON.stringify(payload),
    );
  } catch {
    // A disabled or full localStorage must not break the cockpit.
  }
}

/** Merge a partial update with the current browser-scoped preferences. */
export function updateDashboardPreferences(
  patch: DashboardPreferencesPatch | null | undefined,
  storage: DashboardPreferencesStorage | null | undefined = browserStorage(),
): void {
  const current = loadDashboardPreferences(storage);
  saveDashboardPreferences(
    {
      mode: patch?.mode ?? current.mode,
      singleIndicatorCode: patch?.singleIndicatorCode ?? current.singleIndicatorCode,
      daySorts: patch?.daySorts ?? current.daySorts,
      monthSorts: patch?.monthSorts ?? current.monthSorts,
    },
    storage,
  );
}

/** Keep a saved single-indicator selection usable after the catalog changes. */
export function resolveSingleIndicatorCode(
  savedCode: string | null | undefined,
  availableCodes: readonly string[],
): string {
  const available = availableCodes.filter((code) => typeof code === "string" && code.length > 0);
  const normalizedSavedCode = normalizeIndicatorCode(savedCode);
  return normalizedSavedCode && available.includes(normalizedSavedCode)
    ? normalizedSavedCode
    : available[0] ?? "";
}
