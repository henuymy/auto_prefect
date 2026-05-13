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
        required: ["name", "stage", "method", "url", "body_type"],
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
          excel: { type: "object", additionalProperties: true },
          drilldown: { type: "object", additionalProperties: true },
        },
        additionalProperties: true,
      },
    },
    compare_sources: { type: "array", items: { type: "object", additionalProperties: true } },
    send: {
      type: "object",
      required: ["webhook_url", "workbook_name", "items"],
      properties: {
        webhook_url: { type: "string" },
        workbook_name: { type: "string" },
        items: { type: "array", items: { type: "object", additionalProperties: true } },
      },
      additionalProperties: true,
    },
    template_update: { type: "object", additionalProperties: true },
    wait_for_change: { type: "object", additionalProperties: true },
    deployment: { type: "object", additionalProperties: true },
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
