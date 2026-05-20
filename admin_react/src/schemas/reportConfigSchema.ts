import Ajv, { type ErrorObject } from "ajv";
import type { ReportConfig, ValidationIssue } from "@/types/config";

export const reportConfigJsonSchema = {
  type: "object",
  required: ["name", "template_path", "downloads", "compare_sources", "send", "template_update"],
  properties: {
    id: { type: "string" },
    name: { type: "string", minLength: 1 },
    template_path: { type: "string", minLength: 1 },
    enabled: { type: "boolean" },
    description: { type: "string" },
    downloads: {
      type: "array",
      minItems: 1,
      items: {
        type: "object",
        required: ["name", "stage", "method", "url", "body_type", "response_mode"],
        properties: {
          name: { type: "string", minLength: 1 },
          stage: { type: "string", minLength: 1 },
          auth_preset: { type: "string" },
          method: { enum: ["GET", "POST", "PUT", "PATCH", "DELETE"] },
          url: { type: "string", minLength: 1 },
          headers: { type: "object", additionalProperties: { type: "string" } },
          body_type: { enum: ["form", "json", "raw"] },
          data: { type: "object", additionalProperties: true },
          raw_body: { type: "string" },
          response_mode: { enum: ["file", "json_to_excel", "json_drilldown_to_excel"] },
          headers_from_cookies: { type: "object", additionalProperties: { type: "string" } },
          headers_from_session_storage: { type: "object", additionalProperties: { type: "string" } },
          headers_from_local_storage: { type: "object", additionalProperties: { type: "string" } },
          headers_from_cookie_string: { type: "object", additionalProperties: { type: "string" } },
          excel: {
            type: "object",
            properties: {
              data_path: { type: "string" },
              sheet_name: { type: "string" },
              columns: {
                type: "array",
                items: {
                  type: "object",
                  required: ["field", "header"],
                  properties: {
                    field: { type: "string", minLength: 1 },
                    header: { type: "string", minLength: 1 },
                    type: { enum: ["number", ""] },
                  },
                  additionalProperties: false,
                },
              },
            },
            additionalProperties: false,
          },
          drilldown: {
            type: "object",
            properties: {
              data_path: { type: "string", minLength: 1 },
              request_area_field: { type: "string", minLength: 1 },
              next_area_field: { type: "string", minLength: 1 },
              levels: { type: "array", items: { type: "string", minLength: 1 } },
              max_requests: { type: "number", minimum: 1 },
              max_workers: { type: "number", minimum: 1 },
            },
            additionalProperties: false,
          },
        },
        allOf: [
          {
            if: { properties: { response_mode: { enum: ["json_to_excel", "json_drilldown_to_excel"] } }, required: ["response_mode"] },
            then: {
              required: ["excel"],
              properties: {
                excel: { required: ["columns"], properties: { columns: { minItems: 1 } } },
              },
            },
          },
          {
            if: { properties: { response_mode: { const: "json_drilldown_to_excel" } }, required: ["response_mode"] },
            then: {
              required: ["drilldown"],
              properties: {
                drilldown: { required: ["data_path", "request_area_field", "next_area_field"] },
              },
            },
          },
        ],
        additionalProperties: true,
      },
    },
    compare_sources: {
      type: "array",
      items: {
        type: "object",
        required: ["download_name", "engine", "max_workers", "sheet_mappings"],
        properties: {
          download_name: { type: "string", minLength: 1 },
          engine: { enum: ["openpyxl", "com"] },
          max_workers: { type: "number", minimum: 1 },
          sheet_mappings: {
            type: "array",
            items: {
              type: "object",
              required: ["new_sheet_name", "template_sheet_name"],
              properties: {
                name: { type: "string" },
                new_sheet_name: { type: "string", minLength: 1 },
                template_sheet_name: { type: "string", minLength: 1 },
                header_row: { type: "number", minimum: 1 },
                ignore_columns: { type: "array", items: { type: "string" } },
                key_columns: { type: "array", items: { type: "string" } },
              },
              additionalProperties: false,
            },
          },
        },
        additionalProperties: false,
      },
    },
    send: {
      type: "object",
      required: ["webhook_url", "workbook_name", "items"],
      properties: {
        webhook_url: { type: "string" },
        workbook_name: { type: "string" },
        items: {
          type: "array",
          items: {
            type: "object",
            required: ["type", "sheet"],
            properties: {
              type: { enum: ["image", "text"] },
              sheet: { type: "string", minLength: 1 },
              text: {
                type: "object",
                properties: {
                  mode: { enum: ["used_range", "none"] },
                },
                additionalProperties: false,
              },
            },
            additionalProperties: false,
          },
        },
      },
      additionalProperties: true,
    },
    template_update: {
      type: "object",
      required: ["update_condition", "write_sheets", "send_when_same"],
      properties: {
        engine: { enum: ["hybrid", "com_copy"] },
        update_condition: { enum: ["any_changed", "all_changed"] },
        write_sheets: { enum: ["changed", "all_compared"] },
        send_when_same: { type: "boolean" },
      },
      additionalProperties: false,
    },
    wait_for_change: {
      type: "object",
      required: ["enabled", "poll_interval_seconds", "max_wait_minutes"],
      properties: {
        enabled: { type: "boolean" },
        poll_interval_seconds: { type: "number", minimum: 1 },
        max_wait_minutes: { type: "number", minimum: 1 },
      },
      additionalProperties: false,
    },
    deployment: {
      type: "object",
      required: ["enabled", "cron", "timezone"],
      properties: {
        enabled: { type: "boolean" },
        cron: { type: "string" },
        timezone: { type: "string" },
      },
      additionalProperties: false,
    },
    lastRun: { enum: ["success", "failed", "running", "disabled"] },
    updatedAt: { type: "string" },
  },
  additionalProperties: true,
} as const;

const ajv = new Ajv({ allErrors: true, allowUnionTypes: true });
const validate = ajv.compile(reportConfigJsonSchema);

function toIssue(error: ErrorObject): ValidationIssue {
  const missingProperty = error.keyword === "required" ? (error.params as { missingProperty?: string }).missingProperty : "";
  const path = missingProperty ? `${error.instancePath || ""}/${missingProperty}` : error.instancePath || "/";
  return {
    path,
    message: error.message || "配置不符合 Schema",
  };
}

export function validateReportConfig(config: ReportConfig): ValidationIssue[] {
  const ok = validate(config);
  return ok ? [] : (validate.errors || []).map(toIssue);
}
