import { useState } from "react";
import { AlertCircle, CheckCircle2, WandSparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/form";
import { parseRequestByMode, type RequestParseMode } from "@/lib/requestParser";
import { prettyJson } from "@/lib/utils";
import type { DownloadItem } from "@/types/config";

const MODE_OPTIONS: Array<{ value: RequestParseMode; label: string; hint: string }> = [
  { value: "auto", label: "自动识别", hint: "不确定来源时使用" },
  { value: "curl_bash", label: "cURL bash（推荐）", hint: "Chrome Network 最稳定" },
  { value: "curl_cmd", label: "cURL cmd", hint: "Windows cmd 复制" },
  { value: "http", label: "完整 HTTP 请求", hint: "POST /path HTTP/1.1" },
  { value: "fetch", label: "fetch", hint: "浏览器复制 fetch" },
  { value: "powershell", label: "PowerShell", hint: "复制为 PowerShell" },
  { value: "har", label: "HAR", hint: "复制所有为 HAR" },
  { value: "headers_body", label: "分开填写", hint: "手动填 URL/头/体" },
];

export function RequestParserPanel({ item, onApply }: { item: DownloadItem; onApply: (item: DownloadItem) => void }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<RequestParseMode>("curl_bash");
  const [rawRequest, setRawRequest] = useState("");
  const [splitMethod, setSplitMethod] = useState<DownloadItem["method"]>(item.method || "POST");
  const [splitUrl, setSplitUrl] = useState(item.url || "");
  const [splitHeaders, setSplitHeaders] = useState("");
  const [splitBody, setSplitBody] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<DownloadItem | null>(null);

  const parseAndPreview = () => {
    setError("");
    try {
      const parsed = parseRequestByMode({
        mode,
        rawRequest,
        method: splitMethod,
        url: splitUrl,
        headersText: splitHeaders,
        bodyText: splitBody,
      });
      if (!parsed.url) {
        throw new Error("未识别到请求 URL，请检查复制内容，或切到“分开填写”。");
      }
      const nextHeaders = { ...parsed.headers };
      const next: DownloadItem = {
        ...item,
        method: parsed.method,
        url: parsed.url,
        headers: nextHeaders,
        body_type: parsed.body_type,
        data: parsed.body_type === "raw" ? item.data : parsed.data || {},
        raw_body: parsed.body_type === "raw" ? parsed.raw_body || "" : "",
      };
      applyDetectedAuth(next);
      setPreview(next);
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : "请求识别失败");
    }
  };

  const applyDetectedAuth = (next: DownloadItem) => {
    const headerNames = Object.keys(next.headers || {});
    const findHeader = (expected: string) => headerNames.find((name) => name.toLowerCase() === expected.toLowerCase());
    const removeHeader = (name?: string) => {
      if (name) delete next.headers[name];
    };

    const userInfo = findHeader("User-Info") || findHeader("user-info");
    const uapToken = findHeader("Uaptoken") || findHeader("uapToken");
    const ssrToken = findHeader("Ssr-token") || findHeader("ssr-token");

    delete next.headers_from_cookies;
    delete next.headers_from_session_storage;
    delete next.headers_from_local_storage;
    delete next.headers_from_cookie_string;

    if (userInfo) {
      next.auth_preset = "智慧运营 User-Info";
      next.stage = "smart_ops";
      next.headers_from_session_storage = { "User-Info": "zhyyptInfo.accessToken" };
      removeHeader(userInfo);
      return;
    }
    if (uapToken) {
      next.auth_preset = "地市平台 Uaptoken";
      next.stage = "city_ops";
      next.headers_from_session_storage = { Uaptoken: "uapToken" };
      removeHeader(uapToken);
      return;
    }
    if (ssrToken) {
      next.auth_preset = "报表分析 Ssr-token";
      next.stage = "report_analysis";
      next.headers_from_cookies = { "Ssr-token": "ssr-token" };
      removeHeader(ssrToken);
    }
  };

  const applyPreview = () => {
    if (!preview) return;
    onApply(preview);
  };

  return (
    <div className="mb-4 rounded-2xl border border-dashed border-primary/30 bg-primary/5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span>
          <span className="flex items-center gap-2 text-sm font-black text-foreground">
            <WandSparkles className="h-4 w-4 text-primary" />
            请求识别
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            粘贴浏览器 Network 里的 cURL / HTTP / fetch，自动回填 URL、请求头、请求体和载体类型。
          </span>
        </span>
        <Badge variant="outline">{open ? "收起" : "展开"}</Badge>
      </button>

      {open && (
        <div className="space-y-4 border-t border-primary/20 p-4">
          <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
            <div>
              <Label>解析方式</Label>
              <Select value={mode} onChange={(event) => setMode(event.target.value as RequestParseMode)} className="mt-2">
                {MODE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </Select>
              <p className="mt-2 text-xs text-muted-foreground">{MODE_OPTIONS.find((option) => option.value === mode)?.hint}</p>
            </div>
            {mode !== "headers_body" ? (
              <div>
                <Label>复制内容</Label>
                <Textarea
                  value={rawRequest}
                  onChange={(event) => setRawRequest(event.target.value)}
                  className="mt-2 min-h-40 font-mono text-xs"
                  placeholder="推荐粘贴：Copy as cURL (bash)"
                />
              </div>
            ) : (
              <div className="grid gap-3">
                <div className="grid gap-3 md:grid-cols-[180px_1fr]">
                  <div>
                    <Label>请求方法</Label>
                    <Select value={splitMethod} onChange={(event) => setSplitMethod(event.target.value as DownloadItem["method"])} className="mt-2">
                      <option>POST</option>
                      <option>GET</option>
                      <option>PUT</option>
                      <option>PATCH</option>
                      <option>DELETE</option>
                    </Select>
                  </div>
                  <div>
                    <Label>请求 URL</Label>
                    <Input value={splitUrl} onChange={(event) => setSplitUrl(event.target.value)} className="mt-2" />
                  </div>
                </div>
                <div>
                  <Label>请求头原文</Label>
                  <Textarea value={splitHeaders} onChange={(event) => setSplitHeaders(event.target.value)} className="mt-2 font-mono text-xs" />
                </div>
                <div>
                  <Label>请求载体原文</Label>
                  <Textarea value={splitBody} onChange={(event) => setSplitBody(event.target.value)} className="mt-2 font-mono text-xs" />
                </div>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={parseAndPreview}>识别请求</Button>
            <Button onClick={applyPreview} disabled={!preview}>确认回填到当前抓取项</Button>
            <span className="text-xs text-muted-foreground">默认会忽略 Cookie、Host、Content-Length、sec-* 等浏览器运行时头。</span>
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm font-semibold text-red-500">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}

          {preview && (
            <div className="grid gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 lg:grid-cols-3">
              <div className="lg:col-span-3 flex flex-wrap items-center gap-2 text-sm font-bold text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4" />
                已识别：{preview.method} / {preview.body_type} / {Object.keys(preview.headers || {}).length} 个请求头
              </div>
              <div className="lg:col-span-3 break-all rounded-lg bg-background/80 p-3 font-mono text-xs text-muted-foreground">{preview.url}</div>
              <pre className="max-h-48 overflow-auto rounded-lg bg-background/80 p-3 text-xs">{prettyJson(preview.headers || {})}</pre>
              <pre className="max-h-48 overflow-auto rounded-lg bg-background/80 p-3 text-xs lg:col-span-2">
                {preview.body_type === "raw" ? preview.raw_body : prettyJson(preview.data || {})}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
