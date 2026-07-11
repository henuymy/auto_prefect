import type { DownloadItem } from "@/types/config";

export interface QuickDateField {
  id: string;
  label: string;
  value: string;
  location: "data" | "headers" | "url" | "raw_json" | "raw_form";
  path?: string[];
  queryIndex?: number;
}

export const DATE_PRESETS = [
  { value: "${today_yyyymmdd}", label: "今天 · YYYYMMDD" },
  { value: "${yesterday_yyyymmdd}", label: "昨天 · YYYYMMDD" },
  { value: "${day_before_yesterday_yyyymmdd}", label: "前天 · YYYYMMDD" },
  { value: "${date:yesterday-1M|yyyyMMdd}", label: "上月同期 · YYYYMMDD" },
  { value: "${date:yesterday-1y|yyyyMMdd}", label: "去年同期 · YYYYMMDD" },
  { value: "${today}", label: "今天 · YYYY-MM-DD" },
  { value: "${yesterday}", label: "昨天 · YYYY-MM-DD" },
  { value: "${day_before_yesterday}", label: "前天 · YYYY-MM-DD" },
  { value: "${date:yesterday-1M|yyyy-MM-dd}", label: "上月同期 · YYYY-MM-DD" },
  { value: "${date:yesterday-1y|yyyy-MM-dd}", label: "去年同期 · YYYY-MM-DD" },
] as const;

const DATE_KEY_PATTERN = /(querydate|versionname|bizdate|statdate|startdate|enddate|begindate|start[_-]?time|end[_-]?time|begin[_-]?time|query[_-]?time|stat[_-]?time|timestamp|acctmonth|date|day|month|year|日期|时间|账期|月份)/i;
const DATE_VALUE_PATTERN = /^(?:\$\{(?:today|yesterday|day_before_yesterday)(?:_yyyymmdd)?\}|\$\{date:(?:today|yesterday|day_before_yesterday)(?:[+-]\d+[dMy])*\|yyyy(?:MMdd|-MM-dd)\}|\d{4}[-/]?\d{2}[-/]?\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isDateCandidate(key: string, value: unknown): boolean {
  if (DATE_KEY_PATTERN.test(key)) return value === null || value === undefined || ["string", "number"].includes(typeof value);
  if (typeof value !== "string" && typeof value !== "number") return false;
  return DATE_VALUE_PATTERN.test(String(value).trim());
}

function collectObjectFields(
  value: unknown,
  location: QuickDateField["location"],
  labelPrefix: string,
  path: string[] = [],
): QuickDateField[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => collectObjectFields(item, location, labelPrefix, [...path, String(index)]));
  }
  if (!isRecord(value)) return [];
  return Object.entries(value).flatMap(([key, child]) => {
    const childPath = [...path, key];
    if (isRecord(child) || Array.isArray(child)) return collectObjectFields(child, location, labelPrefix, childPath);
    if (!isDateCandidate(key, child)) return [];
    return [{
      id: `${location}:${childPath.join(".")}`,
      label: `${labelPrefix} · ${childPath.join(".")}`,
      value: String(child ?? ""),
      location,
      path: childPath,
    }];
  });
}

function collectParameterFields(text: string, location: "url" | "raw_form", labelPrefix: string): QuickDateField[] {
  const pattern = location === "url" ? /([?&])([^=&#]+)=([^&#]*)/g : /(^|&)([^=&]+)=([^&]*)/g;
  const fields: QuickDateField[] = [];
  let match: RegExpExecArray | null;
  let queryIndex = 0;
  while ((match = pattern.exec(text)) !== null) {
    const key = decodeURIComponent(match[2]);
    const rawValue = match[3];
    let value = rawValue;
    try {
      value = decodeURIComponent(rawValue);
    } catch {
      // Keep malformed or intentionally unescaped platform values editable.
    }
    if (isDateCandidate(key, value)) {
      fields.push({
        id: `${location}:${queryIndex}:${key}`,
        label: `${labelPrefix} · ${key}`,
        value,
        location,
        queryIndex,
      });
    }
    queryIndex += 1;
  }
  return fields;
}

export function collectQuickDateFields(item: DownloadItem): QuickDateField[] {
  const fields = [
    ...collectObjectFields(item.data || {}, "data", "请求体"),
    ...collectObjectFields(item.headers || {}, "headers", "请求头"),
    ...collectParameterFields(item.url || "", "url", "URL"),
  ];
  const rawBody = item.raw_body?.trim();
  if (rawBody) {
    try {
      fields.push(...collectObjectFields(JSON.parse(rawBody), "raw_json", "Raw JSON"));
    } catch {
      fields.push(...collectParameterFields(rawBody, "raw_form", "Raw Form"));
    }
  }
  return fields;
}

function setNestedValue(root: unknown, path: string[], value: string): unknown {
  if (!path.length) return value;
  const next = structuredClone(root);
  let cursor = next as Record<string, unknown> | unknown[];
  path.forEach((segment, index) => {
    if (index === path.length - 1) {
      if (Array.isArray(cursor)) cursor[Number(segment)] = value;
      else cursor[segment] = value;
      return;
    }
    cursor = (Array.isArray(cursor) ? cursor[Number(segment)] : cursor[segment]) as Record<string, unknown> | unknown[];
  });
  return next;
}

function replaceParameterValue(text: string, field: QuickDateField, value: string): string {
  const pattern = field.location === "url" ? /([?&])([^=&#]+)=([^&#]*)/g : /(^|&)([^=&]+)=([^&]*)/g;
  let queryIndex = 0;
  return text.replace(pattern, (match, prefix: string, key: string) => {
    const currentIndex = queryIndex++;
    if (currentIndex !== field.queryIndex) return match;
    return `${prefix}${key}=${value}`;
  });
}

export function updateQuickDateField(item: DownloadItem, field: QuickDateField, value: string): DownloadItem {
  const next = structuredClone(item);
  if (field.location === "data") {
    next.data = setNestedValue(next.data || {}, field.path || [], value) as Record<string, unknown>;
  } else if (field.location === "headers") {
    next.headers = setNestedValue(next.headers || {}, field.path || [], value) as Record<string, string>;
  } else if (field.location === "url") {
    next.url = replaceParameterValue(next.url || "", field, value);
  } else if (field.location === "raw_json") {
    const parsed = JSON.parse(next.raw_body || "{}");
    next.raw_body = JSON.stringify(setNestedValue(parsed, field.path || [], value), null, 2);
  } else {
    next.raw_body = replaceParameterValue(next.raw_body || "", field, value);
  }
  return next;
}

export function formatCustomDate(date: string, currentValue: string): string {
  if (!date) return currentValue;
  if (/_yyyymmdd\}/i.test(currentValue) || /\|yyyyMMdd\}/.test(currentValue) || /^\d{8}$/.test(currentValue)) return date.replace(/-/g, "");
  if (/^\d{4}\/\d{2}\/\d{2}/.test(currentValue)) return date.replace(/-/g, "/");
  const timeSuffix = currentValue.match(/([ T]\d{2}:\d{2}(?::\d{2})?)$/)?.[1];
  if (timeSuffix) return `${date}${timeSuffix}`;
  return date;
}
