import type { BodyType, DownloadItem } from "@/types/config";

export type RequestParseMode =
  | "auto"
  | "curl_bash"
  | "curl_cmd"
  | "http"
  | "fetch"
  | "powershell"
  | "har"
  | "headers_body";

export interface ParseRequestInput {
  mode: RequestParseMode;
  rawRequest?: string;
  method?: string;
  url?: string;
  headersText?: string;
  bodyText?: string;
}

export interface ParsedRequest {
  method: NonNullable<DownloadItem["method"]>;
  url: string;
  headers: Record<string, string>;
  body_type: BodyType;
  data?: Record<string, unknown>;
  raw_body?: string;
}

const ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
type RequestMethod = (typeof ALLOWED_METHODS)[number];
const SKIP_HEADER_PREFIXES = ["sec-", "proxy-", ":"];
const SKIP_HEADERS = new Set(["cookie", "content-length", "host", "connection", "accept-encoding"]);

function normalizeMethod(value?: string): RequestMethod {
  const upper = (value || "POST").trim().toUpperCase();
  return ALLOWED_METHODS.includes(upper as RequestMethod) ? (upper as RequestMethod) : "POST";
}

function unquote(value: string): string {
  const trimmed = value.trim();
  if ((trimmed.startsWith("'") && trimmed.endsWith("'")) || (trimmed.startsWith('"') && trimmed.endsWith('"'))) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function normalizeLineContinuations(value: string): string {
  return value
    .replace(/\r\n/g, "\n")
    .replace(/\\\n/g, " ")
    .replace(/\^\n/g, " ")
    .replace(/`\n/g, " ");
}

export function shouldSendHeader(name: string): boolean {
  const lower = name.trim().toLowerCase();
  if (!lower) return false;
  if (SKIP_HEADERS.has(lower)) return false;
  return !SKIP_HEADER_PREFIXES.some((prefix) => lower.startsWith(prefix));
}

export function filterSendableHeaders(headers: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(headers).filter(([name]) => shouldSendHeader(name)));
}

export function parseHeadersText(text?: string): Record<string, string> {
  const source = (text || "").trim();
  if (!source) return {};

  try {
    const parsed = JSON.parse(source);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.fromEntries(Object.entries(parsed).map(([key, value]) => [key, String(value)]));
    }
  } catch {
    // Fall through to "Header: value" parsing.
  }

  const headers: Record<string, string> = {};
  source.split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || !trimmed.includes(":")) return;
    const index = trimmed.indexOf(":");
    const name = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim();
    if (name) headers[name] = value;
  });
  return headers;
}

function inferBodyType(headers: Record<string, string>, body?: string): BodyType {
  const contentType = Object.entries(headers).find(([name]) => name.toLowerCase() === "content-type")?.[1]?.toLowerCase() || "";
  const source = (body || "").trim();
  if (contentType.includes("application/json")) return "json";
  if (contentType.includes("application/x-www-form-urlencoded")) return "form";
  if (!source) return "json";
  if (source.startsWith("{") || source.startsWith("[")) return "json";
  if (source.includes("=") && (source.includes("&") || !source.includes("{"))) return "form";
  return "raw";
}

function parseBodyPayload(bodyType: BodyType, body?: string): { data?: Record<string, unknown>; raw_body?: string } {
  const source = (body || "").trim();
  if (!source) return { data: {} };

  if (bodyType === "json") {
    const parsed = JSON.parse(source);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return { data: parsed };
    return { data: { value: parsed } };
  }

  if (bodyType === "form") {
    const params = new URLSearchParams(source);
    const data: Record<string, unknown> = {};
    params.forEach((value, key) => {
      data[key] = value;
    });
    return { data };
  }

  return { raw_body: source };
}

function splitCommandArgs(command: string): string[] {
  const args: string[] = [];
  let current = "";
  let quote: "'" | '"' | null = null;
  let escaped = false;

  for (const char of normalizeLineContinuations(command)) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if ((char === "'" || char === '"') && !quote) {
      quote = char;
      continue;
    }
    if (char === quote) {
      quote = null;
      continue;
    }
    if (/\s/.test(char) && !quote) {
      if (current) {
        args.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }
  if (current) args.push(current);
  return args;
}

function parseCurl(raw: string): Partial<ParsedRequest> & { body?: string } {
  const args = splitCommandArgs(raw);
  const headers: Record<string, string> = {};
  let method = "POST";
  let url = "";
  let body = "";

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    const next = args[index + 1] || "";
    if (arg === "curl" || arg === "curl.exe") continue;
    if (arg === "-H" || arg === "--header") {
      const header = unquote(next);
      const divider = header.indexOf(":");
      if (divider > 0) headers[header.slice(0, divider).trim()] = header.slice(divider + 1).trim();
      index += 1;
      continue;
    }
    if (arg === "-b" || arg === "--cookie") {
      index += 1;
      continue;
    }
    if (arg === "-X" || arg === "--request") {
      method = next;
      index += 1;
      continue;
    }
    if (["--data", "--data-raw", "--data-binary", "--data-urlencode", "-d"].includes(arg)) {
      body = unquote(next);
      method = method || "POST";
      index += 1;
      continue;
    }
    if (!arg.startsWith("-") && /^https?:\/\//i.test(unquote(arg))) {
      url = unquote(arg);
    }
  }
  return { method: normalizeMethod(method), url, headers, body };
}

function parseHttp(raw: string): Partial<ParsedRequest> & { body?: string } {
  const normalized = raw.replace(/\r\n/g, "\n");
  const [head, ...bodyParts] = normalized.split(/\n\s*\n/);
  const lines = head.split("\n").map((line) => line.trim()).filter(Boolean);
  const first = lines.shift() || "";
  const match = first.match(/^([A-Z]+)\s+(\S+)/i);
  const headers = parseHeadersText(lines.join("\n"));
  const host = headers.Host || headers.host || "";
  let url = match?.[2] || "";
  if (url && !/^https?:\/\//i.test(url) && host) {
    url = `${host.includes(":") ? "https" : "https"}://${host}${url}`;
  }
  return { method: normalizeMethod(match?.[1]), url, headers, body: bodyParts.join("\n\n").trim() };
}

function parseFetch(raw: string): Partial<ParsedRequest> & { body?: string } {
  const url = raw.match(/fetch\(\s*["'`](.*?)["'`]/s)?.[1] || "";
  const method = raw.match(/method\s*:\s*["'`](.*?)["'`]/s)?.[1] || "GET";
  const body = raw.match(/body\s*:\s*JSON\.stringify\((\{[\s\S]*?\})\)/s)?.[1] || raw.match(/body\s*:\s*["'`]([\s\S]*?)["'`]/s)?.[1] || "";
  const headersBlock = raw.match(/headers\s*:\s*(new Headers\()?(\{[\s\S]*?\})\)?/s)?.[2] || "";
  let headers: Record<string, string> = {};
  if (headersBlock) {
    try {
      headers = JSON.parse(headersBlock.replace(/([{,]\s*)([A-Za-z0-9_-]+)\s*:/g, '$1"$2":').replace(/'/g, '"'));
    } catch {
      headers = parseHeadersText(headersBlock.replace(/[{},]/g, "\n"));
    }
  }
  return { method: normalizeMethod(method), url, headers, body };
}

function parsePowershell(raw: string): Partial<ParsedRequest> & { body?: string } {
  const normalized = normalizeLineContinuations(raw);
  const url = normalized.match(/-(?:Uri|Url)\s+["']([^"']+)["']/i)?.[1] || "";
  const method = normalized.match(/-Method\s+["']?([A-Z]+)["']?/i)?.[1] || "POST";
  const body = normalized.match(/-Body\s+['"]([\s\S]*?)['"](?:\s+-|$)/i)?.[1] || "";
  const headersBlock = normalized.match(/-Headers\s+@\{([\s\S]*?)\}/i)?.[1] || "";
  const headers: Record<string, string> = {};
  headersBlock.split(";").forEach((line) => {
    const match = line.match(/["']?([^="'\s]+)["']?\s*=\s*["']([^"']*)["']/);
    if (match) headers[match[1]] = match[2];
  });
  return { method: normalizeMethod(method), url, headers, body };
}

function parseHar(raw: string): Partial<ParsedRequest> & { body?: string } {
  const payload = JSON.parse(raw);
  const request = payload?.log?.entries?.[0]?.request || payload?.request || payload;
  const headers = Object.fromEntries((request.headers || []).map((item: { name: string; value: string }) => [item.name, item.value]));
  return {
    method: normalizeMethod(request.method),
    url: request.url || "",
    headers,
    body: request.postData?.text || "",
  };
}

function parseAuto(raw: string): Partial<ParsedRequest> & { body?: string } {
  const trimmed = raw.trim();
  if (/^curl(\.exe)?\s/i.test(trimmed)) return parseCurl(trimmed);
  if (/^POST\s|^GET\s|^PUT\s|^PATCH\s|^DELETE\s/i.test(trimmed)) return parseHttp(trimmed);
  if (/fetch\(/.test(trimmed)) return parseFetch(trimmed);
  if (/Invoke-(WebRequest|RestMethod)/i.test(trimmed)) return parsePowershell(trimmed);
  if (trimmed.startsWith("{") && trimmed.includes('"request"')) return parseHar(trimmed);
  return parseHttp(trimmed);
}

export function parseRequestByMode(input: ParseRequestInput): ParsedRequest {
  const raw = input.rawRequest || "";
  const parsed =
    input.mode === "headers_body"
      ? { method: normalizeMethod(input.method), url: input.url || "", headers: parseHeadersText(input.headersText), body: input.bodyText || "" }
      : input.mode === "curl_bash" || input.mode === "curl_cmd"
        ? parseCurl(raw)
        : input.mode === "http"
          ? parseHttp(raw)
          : input.mode === "fetch"
            ? parseFetch(raw)
            : input.mode === "powershell"
              ? parsePowershell(raw)
              : input.mode === "har"
                ? parseHar(raw)
                : parseAuto(raw);

  const headers = filterSendableHeaders(parsed.headers || {});
  const body = parsed.body || "";
  const body_type = inferBodyType(headers, body);
  const bodyPayload = parseBodyPayload(body_type, body);

  return {
    method: normalizeMethod(parsed.method),
    url: parsed.url || input.url || "",
    headers,
    body_type,
    ...bodyPayload,
  };
}
