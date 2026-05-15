import indicatorNameMap from "@/data/indicatorNameMap.json";
import type { ExcelColumn } from "@/types/config";

export interface ResponseParseResult {
  data_path: string;
  rowCount: number;
  columns: ExcelColumn[];
  sampleRows: Array<Record<string, unknown>>;
  kind?: "table" | "indicator_mapping";
  indicatorNames?: Record<string, string>;
}

const DEFAULT_SYSTEM_COLUMNS: ExcelColumn[] = [
  { field: "__level_name", header: "层级" },
  { field: "__parent_area_id", header: "父级areaId" },
  { field: "__request_area_id", header: "请求areaId" },
];
const LEADING_DATA_FIELDS = ["areaName", "areaCode"];
const STATIC_INDICATOR_NAMES = indicatorNameMap as Record<string, string>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isScalar(value: unknown) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function collectIndicatorPairs(value: unknown, result = new Map<string, string>()) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectIndicatorPairs(item, result));
    return result;
  }
  if (isRecord(value)) {
    const code = value.indCode;
    const name = value.indName;
    if (typeof code === "string" && code.trim() && typeof name === "string" && name.trim()) {
      result.set(code.trim(), name.trim());
    }
    Object.values(value).forEach((child) => collectIndicatorPairs(child, result));
  }
  return result;
}

export function parseIndicatorMapping(text: string): Record<string, string> {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("请先粘贴指标映射 JSON。");
  }

  let payload: unknown;
  try {
    payload = JSON.parse(trimmed);
  } catch {
    throw new Error("指标映射不是有效 JSON，请检查格式。");
  }

  const mapping = collectIndicatorPairs(payload);
  if (!mapping.size && isRecord(payload)) {
    Object.entries(payload).forEach(([key, value]) => {
      if (typeof value === "string" && key.trim() && value.trim()) {
        mapping.set(key.trim(), value.trim());
      }
    });
  }
  if (!mapping.size) {
    throw new Error("没有识别到 indCode/indName 或 code:name 映射。");
  }
  return Object.fromEntries(mapping);
}

function isIndicatorDictionaryRows(rows: Array<Record<string, unknown>>) {
  return rows.length > 0 && rows.every((row) => typeof row.indCode === "string" && typeof row.indName === "string");
}

function walkArrays(value: unknown, indicatorNames: Map<string, string>, path: string[] = [], results: ResponseParseResult[] = []) {
  if (Array.isArray(value)) {
    const rows = value.filter(isRecord);
    if (rows.length) {
      const fields = Array.from(
        new Set(
          rows
            .slice(0, 20)
            .flatMap((row) => Object.entries(row).filter(([, fieldValue]) => isScalar(fieldValue)).map(([key]) => key)),
        ),
      );
      if (fields.length) {
        results.push({
          data_path: path.length ? path.join(".") : ".",
          rowCount: value.length,
          columns: fields.map((field) => ({ field, header: indicatorNames.get(field) || field })),
          sampleRows: rows.slice(0, 3),
          kind: isIndicatorDictionaryRows(rows) ? "indicator_mapping" : "table",
        });
      }
    }
    value.slice(0, 5).forEach((item, index) => walkArrays(item, indicatorNames, [...path, String(index)], results));
    return results;
  }

  if (isRecord(value)) {
    Object.entries(value).forEach(([key, child]) => walkArrays(child, indicatorNames, [...path, key], results));
  }
  return results;
}

function score(result: ResponseParseResult) {
  const pathBonus = /table|data|list|rows|result|records/i.test(result.data_path) ? 100 : 0;
  const dictionaryPenalty = result.kind === "indicator_mapping" ? -1000 : 0;
  return result.rowCount * 10 + result.columns.length + pathBonus + dictionaryPenalty;
}

function indicatorMappingResult(indicatorNames: Map<string, string>): ResponseParseResult | null {
  if (!indicatorNames.size) return null;
  return {
    data_path: "result.tableData",
    rowCount: indicatorNames.size,
    columns: Array.from(indicatorNames.entries()).map(([field, header]) => ({ field, header })),
    sampleRows: Array.from(indicatorNames.entries()).slice(0, 3).map(([indCode, indName]) => ({ indCode, indName })),
    kind: "indicator_mapping",
  };
}

function sortColumns(columns: ExcelColumn[]) {
  const rank = (column: ExcelColumn) => {
    if (DEFAULT_SYSTEM_COLUMNS.some((systemColumn) => systemColumn.field === column.field)) {
      return 0;
    }
    if (LEADING_DATA_FIELDS.includes(column.field)) {
      return 1;
    }
    return 2;
  };
  return columns
    .map((column, index) => ({ column, index }))
    .sort((left, right) => rank(left.column) - rank(right.column) || left.index - right.index)
    .map((item) => item.column);
}

export function parseResponseSample(text: string, cachedIndicatorNames: Record<string, string> = {}): ResponseParseResult {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("请先粘贴接口响应 JSON。");
  }

  let payload: unknown;
  try {
    payload = JSON.parse(trimmed);
  } catch {
    throw new Error("响应内容不是有效 JSON，请检查是否复制了完整响应。");
  }

  const indicatorNames = new Map(Object.entries({ ...STATIC_INDICATOR_NAMES, ...cachedIndicatorNames }));
  collectIndicatorPairs(payload, indicatorNames);
  const candidates = walkArrays(payload, indicatorNames).sort((left, right) => score(right) - score(left));
  const tableCandidate = candidates.find((candidate) => candidate.kind !== "indicator_mapping");
  const best = tableCandidate || indicatorMappingResult(indicatorNames) || candidates[0];
  if (!best) {
    throw new Error("没有找到可转成 Excel 的对象数组。需要类似 result.tableData: [{...}] 的结构。");
  }
  const existingFields = new Set(best.columns.map((column) => column.field));
  const columns = [
    ...DEFAULT_SYSTEM_COLUMNS.filter((column) => !existingFields.has(column.field)),
    ...best.columns,
  ];
  return {
    ...best,
    columns: sortColumns(columns),
    indicatorNames: Object.fromEntries(indicatorNames),
  };
}
