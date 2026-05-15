import { useEffect, useRef, useState, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { toast } from "sonner";
import { AlertCircle, ArrowDown, ArrowUp, Bell, CalendarClock, ChevronDown, CloudDownload, GitCompareArrows, Info, Plus, Settings, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RequestParserPanel } from "@/components/config-form/RequestParserPanel";
import { ResponseParserPanel } from "@/components/config-form/ResponseParserPanel";
import { Input, Label, Select, Textarea } from "@/components/ui/form";
import { Tabs } from "@/components/ui/tabs";
import { listTemplates, uploadTemplate } from "@/lib/api";
import { parseJsonSafe, prettyJson } from "@/lib/utils";
import type { CompareSource, DownloadItem, ExcelColumn, ReportConfig, SendItem } from "@/types/config";

const basicSchema = z.object({
  name: z.string().min(1, "配置名称不能为空"),
  template_path: z.string().min(1, "模板路径不能为空"),
  description: z.string().optional(),
  enabled: z.boolean(),
});

type BasicForm = z.infer<typeof basicSchema>;
export type ConfigFormTab = "base" | "downloads" | "compare" | "send" | "advanced";

function clone<T>(value: T): T {
  return structuredClone(value);
}

function updateAt<T>(items: T[], index: number, next: T): T[] {
  return items.map((item, itemIndex) => (itemIndex === index ? next : item));
}

function removeAt<T>(items: T[], index: number): T[] {
  return items.filter((_, itemIndex) => itemIndex !== index);
}

function moveAt<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  if (toIndex < 0 || toIndex >= items.length || fromIndex === toIndex) return items;
  const next = [...items];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

const FIXED_DRILLDOWN = {
  data_path: "result.tableData",
  request_area_field: "areaId",
  next_area_field: "areaCode",
  levels: ["区县", "网格", "渠道/门店", "人员"],
};

function defaultDrilldownConfig(max_requests = 1000, max_workers = 6): NonNullable<DownloadItem["drilldown"]> {
  return {
    ...FIXED_DRILLDOWN,
    max_requests,
    max_workers,
  };
}

function ensureDrilldown(item: DownloadItem): DownloadItem {
  if (item.response_mode !== "json_drilldown_to_excel" || item.drilldown) return item;
  return { ...item, drilldown: defaultDrilldownConfig() };
}

function parseObject(text: string, fallback: Record<string, unknown>) {
  const parsed = parseJsonSafe<Record<string, unknown>>(text);
  return parsed.ok && parsed.value && typeof parsed.value === "object" && !Array.isArray(parsed.value) ? parsed.value : fallback;
}

const PLACEHOLDER_TIPS = [
  "${today}: 今天 YYYY-MM-DD",
  "${yesterday}: 昨天 YYYY-MM-DD",
  "${today_yyyymmdd}: 今天 YYYYMMDD",
  "${yesterday_yyyymmdd}: 昨天 YYYYMMDD",
  "${hour}: 当前小时 0-23",
  "${hour2}: 当前小时 00-23",
];

const AUTH_PRESET_OPTIONS = [
  { value: "报表分析 Ssr-token", label: "报表分析 Ssr-token", desc: "自动配置 Ssr-token: ssr-token。" },
  { value: "智慧运营 User-Info", label: "智慧运营 User-Info", desc: "自动配置 User-Info: zhyyptInfo.accessToken。" },
  { value: "地市平台 Uaptoken", label: "地市平台 Uaptoken", desc: "自动配置 Uaptoken: uapToken。" },
  { value: "自定义", label: "自定义", desc: "保留手工填写的动态认证 JSON，适合特殊请求头或多个 token。" },
  { value: "无", label: "无", desc: "清空动态认证配置，只使用 Cookie Stage 自动带 Cookie。" },
];

function normalizeAuthPreset(value?: string) {
  if (value === "SSR Cookie") return "报表分析 Ssr-token";
  if (value === "地市作战 uapToken") return "地市平台 Uaptoken";
  if (value === "自定义高级") return "自定义";
  return value || "无";
}

function getAuthMapping(item: DownloadItem) {
  return (
    item.headers_from_session_storage ||
    item.headers_from_cookies ||
    item.headers_from_cookie_string ||
    item.headers_from_local_storage ||
    {}
  );
}

function getAuthTargetLabel(item: DownloadItem) {
  const preset = normalizeAuthPreset(item.auth_preset);
  if (preset === "报表分析 Ssr-token") return "当前会写入 headers_from_cookies";
  if (preset === "智慧运营 User-Info" || preset === "地市平台 Uaptoken") return "当前会写入 headers_from_session_storage";
  if (preset === "自定义") return "当前保留自定义动态认证配置";
  return "当前不写入动态认证字段";
}

export function ConfigForm({
  config,
  tab,
  onTabChange,
  onChange,
}: {
  config: ReportConfig;
  tab: ConfigFormTab;
  onTabChange: (tab: ConfigFormTab) => void;
  onChange: (config: ReportConfig) => void;
}) {
  const tabs = [
    { value: "base", label: "基础信息", icon: <Info className="h-4 w-4" /> },
    { value: "downloads", label: "数据抓取", icon: <CloudDownload className="h-4 w-4" /> },
    { value: "compare", label: "报表比对", icon: <GitCompareArrows className="h-4 w-4" /> },
    { value: "send", label: "图文推送", icon: <Bell className="h-4 w-4" /> },
    { value: "advanced", label: "高级策略", icon: <Settings className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-5">
      <Tabs tabs={tabs} value={tab} onChange={(value) => onTabChange(value as ConfigFormTab)} />
      {tab === "base" && <BaseTab config={config} onChange={onChange} />}
      {tab === "downloads" && <DownloadsTab config={config} onChange={onChange} />}
      {tab === "compare" && <CompareTab config={config} onChange={onChange} />}
      {tab === "send" && <SendTab config={config} onChange={onChange} />}
      {tab === "advanced" && <AdvancedTab config={config} onChange={onChange} />}
    </div>
  );
}

function BaseTab({ config, onChange }: { config: ReportConfig; onChange: (config: ReportConfig) => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templates, setTemplates] = useState<Array<{ filename: string; path: string; size: number; modifiedAt: number }>>([]);
  const [nameDraft, setNameDraft] = useState(config.name);
  const [descriptionDraft, setDescriptionDraft] = useState(config.description || "");
  const { register, formState } = useForm<BasicForm>({
    resolver: zodResolver(basicSchema),
    values: {
      name: config.name,
      template_path: config.template_path,
      description: config.description || "",
      enabled: config.enabled !== false,
    },
  });

  useEffect(() => {
    void refreshTemplates();
  }, []);

  useEffect(() => {
    setNameDraft(config.name);
    setDescriptionDraft(config.description || "");
  }, [config.id, config.name, config.description]);

  function commitBaseDrafts() {
    const nextName = nameDraft.trim() || config.name;
    const nextDescription = descriptionDraft;
    if (nextName !== config.name || nextDescription !== (config.description || "")) {
      onChange({ ...config, name: nextName, description: nextDescription });
      if (nextName !== nameDraft) {
        setNameDraft(nextName);
      }
    }
  }

  async function refreshTemplates() {
    setTemplatesLoading(true);
    try {
      setTemplates(await listTemplates());
    } catch {
      // Keep manual typing possible when backend is not available.
    } finally {
      setTemplatesLoading(false);
    }
  }

  async function handleTemplateUpload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    try {
      const result = await uploadTemplate(file);
      onChange({ ...config, template_path: result.path });
      await refreshTemplates();
      toast.success("模板已上传", { description: result.path });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      toast.error("模板上传失败", { description: message.length > 180 ? `${message.slice(0, 177)}...` : message, duration: 2400 });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>基础属性</CardTitle>
        <CardDescription>定义配置名称、模板文件和启用状态，这些字段会直接写入报表 JSON。</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5 md:grid-cols-2">
        <Field label="配置名称 name" error={formState.errors.name?.message}>
          <Input
            value={nameDraft}
            onChange={(event) => setNameDraft(event.target.value)}
            onBlur={commitBaseDrafts}
          />
        </Field>
        <Field label="模板路径 template_path" error={formState.errors.template_path?.message}>
          <Select
            value={config.template_path}
            onFocus={() => void refreshTemplates()}
            onChange={(event) => onChange({ ...config, template_path: event.target.value })}
            disabled={templatesLoading && !templates.length}
          >
            <option value="">{templatesLoading ? "正在读取模板..." : "请选择模板"}</option>
            {config.template_path && !templates.some((template) => template.path === config.template_path) && (
              <option value={config.template_path}>{config.template_path}</option>
            )}
            {templates.map((template) => (
              <option key={template.path} value={template.path}>
                {template.filename}
              </option>
            ))}
          </Select>
          {config.template_path && <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{config.template_path}</p>}
        </Field>
        <Field label="启用定时调度">
          <label className="flex h-10 items-center gap-3 rounded-lg border border-border px-3">
            <input type="checkbox" checked={config.enabled !== false} onChange={(event) => onChange({ ...config, enabled: event.target.checked })} />
            <span className="text-sm text-muted-foreground">{config.enabled === false ? "关闭后保留配置，但不会自动定时运行" : "开启后会按 Cron 自动运行"}</span>
          </label>
        </Field>
        <Field label="模板上传">
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".xlsx,.xlsm,.xls"
            onChange={(event) => void handleTemplateUpload(event.target.files?.[0])}
          />
          <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            <Upload className="h-4 w-4" />
            {uploading ? "上传中" : "上传模板"}
          </Button>
        </Field>
        <Field label="描述 description" className="md:col-span-2">
          <Textarea
            value={descriptionDraft}
            onChange={(event) => setDescriptionDraft(event.target.value)}
            onBlur={commitBaseDrafts}
          />
        </Field>
      </CardContent>
    </Card>
  );
}

function DownloadsTab({ config, onChange }: { config: ReportConfig; onChange: (config: ReportConfig) => void }) {
  const add = () => {
    const next: DownloadItem = {
      name: `抓取项-${config.downloads.length + 1}`,
      stage: "report_analysis",
      auth_preset: "无",
      method: "POST",
      url: "",
      headers: {},
      body_type: "json",
      response_mode: "file",
      data: {},
    };
    onChange({ ...config, downloads: [...config.downloads, next] });
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <CardTitle>数据抓取</CardTitle>
          <CardDescription>每一张卡就是一个下载/JSON 转 Excel 请求，支持 Cookie Stage 和动态认证。</CardDescription>
        </div>
        <Button className="self-start sm:self-auto" variant="outline" onClick={add}><Plus className="h-4 w-4" />添加抓取项</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <PlaceholderGuide />
        {config.downloads.map((item, index) => (
          <DownloadCard
            key={`${item.name}-${index}`}
            item={item}
            index={index}
            onChange={(next) => onChange({ ...config, downloads: updateAt(config.downloads, index, next) })}
            onDelete={() => onChange({ ...config, downloads: config.downloads.filter((_, itemIndex) => itemIndex !== index) })}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function PlaceholderGuide() {
  return (
    <details className="group overflow-hidden rounded-xl border border-dashed border-sky-200/80 bg-gradient-to-r from-sky-50/80 via-background to-cyan-50/60 text-xs text-muted-foreground dark:border-sky-500/30 dark:from-sky-950/25 dark:via-background dark:to-cyan-950/20">
      <summary className="flex cursor-pointer list-none flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sky-500/10 text-sky-600 dark:text-sky-300">{"{}"}</span>
          <div className="min-w-0">
            <div className="font-semibold text-foreground">可用占位符</div>
            <div>用于下载 URL、请求头、请求体和动态认证配置。</div>
          </div>
        </div>
        <span className="self-start rounded-full border border-border bg-background px-2 py-1 text-[11px] transition group-open:bg-sky-500 group-open:text-white sm:self-auto">
          展开查看
        </span>
      </summary>
      <div className="border-t border-sky-100/80 px-4 py-3 dark:border-sky-500/20">
        <div className="flex flex-wrap gap-2">
          {PLACEHOLDER_TIPS.map((tip) => {
            const [name, desc] = tip.split(": ");
            return (
              <code key={tip} className="rounded-full border border-sky-100 bg-white/80 px-2.5 py-1 text-[11px] text-sky-700 shadow-sm dark:border-sky-500/20 dark:bg-sky-950/70 dark:text-sky-100">
                <span className="font-semibold">{name}</span>
                <span className="ml-1 text-muted-foreground">{desc}</span>
              </code>
            );
          })}
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          <div className="rounded-xl border border-sky-100 bg-white/70 p-3 dark:border-sky-500/20 dark:bg-sky-950/40">
            <div className="font-semibold text-foreground">动态认证 JSON 怎么理解</div>
            <p className="mt-1 leading-5">
              运行时从 Cookie、sessionStorage 或 localStorage 取值，再写入指定请求头。格式是
              <code className="mx-1 rounded bg-sky-100 px-1 py-0.5 text-sky-700 dark:bg-sky-900 dark:text-sky-100">{"{\"请求头名\":\"Storage路径\"}"}</code>
              ，例如
              <code className="mx-1 rounded bg-sky-100 px-1 py-0.5 text-sky-700 dark:bg-sky-900 dark:text-sky-100">{"{\"uapToken\":\"uapToken\"}"}</code>
              。普通报表保持 <code className="rounded bg-sky-100 px-1 py-0.5 text-sky-700 dark:bg-sky-900 dark:text-sky-100">{"{}"}</code>。
            </p>
          </div>
          <div className="rounded-xl border border-sky-100 bg-white/70 p-3 dark:border-sky-500/20 dark:bg-sky-950/40">
            <div className="font-semibold text-foreground">认证预设说明</div>
            <div className="mt-2 space-y-1.5">
              {AUTH_PRESET_OPTIONS.map((option) => (
                <div key={option.value} className="flex min-w-0 flex-wrap gap-2 leading-5">
                  <Badge className="shrink-0" variant="outline">{option.label}</Badge>
                  <span>{option.desc}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </details>
  );
}

function DownloadCard({ item, index, onChange, onDelete }: { item: DownloadItem; index: number; onChange: (item: DownloadItem) => void; onDelete: () => void }) {
  const [open, setOpen] = useState(index === 0);
  const [headersText, setHeadersText] = useState(prettyJson(item.headers || {}));
  const [dataText, setDataText] = useState(prettyJson(item.data || {}));
  const [authText, setAuthText] = useState(prettyJson(getAuthMapping(item)));
  const headersError = headersText.trim() ? parseJsonSafe(headersText).ok ? "" : "请求头 JSON 格式错误" : "";
  const dataError = dataText.trim() ? parseJsonSafe(dataText).ok ? "" : "请求体 JSON 格式错误" : "";
  const authError = authText.trim() ? parseJsonSafe(authText).ok ? "" : "动态认证 JSON 格式错误" : "";
  const hasJsonError = Boolean(headersError || dataError || authError);

  useEffect(() => {
    setHeadersText(prettyJson(item.headers || {}));
    setDataText(prettyJson(item.data || {}));
    setAuthText(prettyJson(getAuthMapping(item)));
  }, [item]);

  const applyJsonTexts = () => {
    if (hasJsonError) return;
    const next = clone(item);
    next.headers = parseObject(headersText, item.headers || {}) as Record<string, string>;
    next.data = parseObject(dataText, item.data || {});
    const authMapping = parseObject(authText, getAuthMapping(item)) as Record<string, string>;
    delete next.headers_from_cookies;
    delete next.headers_from_session_storage;
    delete next.headers_from_local_storage;
    delete next.headers_from_cookie_string;
    if (normalizeAuthPreset(item.auth_preset) === "报表分析 Ssr-token") {
      next.headers_from_cookies = authMapping;
    } else if (normalizeAuthPreset(item.auth_preset) === "无") {
      // Keep dynamic auth empty for normal cookie-only downloads.
    } else {
      next.headers_from_session_storage = authMapping;
    }
    onChange(next);
  };

  const applyAuthPreset = (authPreset: string) => {
    if (authPreset === "自定义") {
      onChange({ ...item, auth_preset: authPreset });
      return;
    }

    const next = clone(item);
    next.auth_preset = authPreset;
    delete next.headers_from_cookies;
    delete next.headers_from_session_storage;
    delete next.headers_from_local_storage;
    delete next.headers_from_cookie_string;

    if (authPreset === "报表分析 Ssr-token") {
      next.headers_from_cookies = { "Ssr-token": "ssr-token" };
    } else if (authPreset === "智慧运营 User-Info") {
      next.headers_from_session_storage = { "User-Info": "zhyyptInfo.accessToken" };
    } else if (authPreset === "地市平台 Uaptoken") {
      next.headers_from_session_storage = { Uaptoken: "uapToken" };
    }

    setAuthText(prettyJson(next.headers_from_session_storage || next.headers_from_cookies || {}));
    onChange(next);
  };

  return (
    <div className="rounded-xl border border-border bg-muted/20 p-3 sm:p-4">
      <div className="mb-4 flex items-start justify-between gap-4">
        <button type="button" onClick={() => setOpen((value) => !value)} className="min-w-0 flex-1 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">#{index + 1}</Badge>
          <Badge>{item.response_mode || "response_mode 未填"}</Badge>
            <Badge variant="outline">{item.stage}</Badge>
            <ChevronDown className={`h-4 w-4 text-muted-foreground transition ${open ? "rotate-180" : ""}`} />
          </div>
          <h3 className="mt-2 text-base font-black">{item.name || "未命名抓取项"}</h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{item.url || "尚未填写 URL"}</p>
        </button>
        <Button variant="ghost" size="icon" onClick={onDelete} disabled={index === 0}><Trash2 className="h-4 w-4 text-red-500" /></Button>
      </div>
      {!open && (
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <Badge variant="outline">{item.method || "method 未填"}</Badge>
          <Badge variant="outline">{item.body_type || "body_type 未填"}</Badge>
          <Badge variant="outline">{normalizeAuthPreset(item.auth_preset)}</Badge>
        </div>
      )}
      {open && (
        <>
      <RequestParserPanel item={item} onApply={onChange} />
      <ResponseParserPanel item={item} onApply={onChange} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Field label="下载标识 name"><DraftInput value={item.name} onCommit={(value) => onChange({ ...item, name: value })} /></Field>
        <Field label="Cookie Stage"><Select value={item.stage} onChange={(event) => onChange({ ...item, stage: event.target.value })}><option>report_analysis</option><option>smart_ops</option><option>city_ops</option><option>data_market</option></Select></Field>
        <Field label="响应模式 response_mode"><Select value={item.response_mode || ""} onChange={(event) => onChange(ensureDrilldown({ ...item, response_mode: event.target.value as DownloadItem["response_mode"] }))}><option value="" disabled>请选择响应模式</option><option value="file">file</option><option value="json_to_excel">json_to_excel</option><option value="json_drilldown_to_excel">json_drilldown_to_excel</option></Select></Field>
        <Field label="请求方法 method"><Select value={item.method || ""} onChange={(event) => onChange({ ...item, method: event.target.value as DownloadItem["method"] })}><option value="" disabled>请选择请求方法</option><option>POST</option><option>GET</option><option>PUT</option><option>PATCH</option><option>DELETE</option></Select></Field>
        <Field label="载体类型 body_type"><Select value={item.body_type || ""} onChange={(event) => onChange({ ...item, body_type: event.target.value as DownloadItem["body_type"] })}><option value="" disabled>请选择载体类型</option><option>json</option><option>form</option><option>raw</option></Select></Field>
        <Field label="认证预设 auth_preset">
          <Select value={normalizeAuthPreset(item.auth_preset)} onChange={(event) => applyAuthPreset(event.target.value)}>
            {AUTH_PRESET_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </Select>
          <p className="mt-1 text-xs text-muted-foreground">{getAuthTargetLabel(item)}</p>
        </Field>
        <Field label="下载 URL" hint="可以写占位符，例如 queryDate=${today_yyyymmdd}" className="lg:col-span-3"><DraftInput value={item.url} onCommit={(value) => onChange({ ...item, url: value })} /></Field>
        <JsonTextField label="请求头 Headers JSON" value={headersText} error={headersError} onChange={setHeadersText} onBlur={applyJsonTexts} />
        <JsonTextField label="请求体 data/json" value={dataText} error={dataError} onChange={setDataText} onBlur={applyJsonTexts} />
        <JsonTextField label="动态认证 JSON" value={authText} error={authError} onChange={setAuthText} onBlur={applyJsonTexts} />
      </div>
      <ResponseModeConfig item={item} onChange={onChange} />
        </>
      )}
    </div>
  );
}

function ResponseModeConfig({ item, onChange }: { item: DownloadItem; onChange: (item: DownloadItem) => void }) {
  const mode = item.response_mode;
  if (mode === "file") {
    return (
      <div className="mt-4 rounded-xl border border-border bg-background/60 p-3 text-xs text-muted-foreground">
        当前为原始文件下载模式，接口响应会直接保存为 Excel/文件，不需要配置 JSON 转 Excel。
      </div>
    );
  }

  const excel = item.excel || {};
  const columns = excel.columns || [];
  const updateExcel = (nextExcel: NonNullable<DownloadItem["excel"]>) => onChange({ ...item, excel: nextExcel });
  const updateColumn = (index: number, column: ExcelColumn) => updateExcel({ ...excel, columns: updateAt(columns, index, column) });

  return (
    <div className="mt-4 space-y-4 rounded-2xl border border-primary/15 bg-background/70 p-4">
      <div>
        <div className="text-sm font-black">JSON 转 Excel 配置</div>
        <p className="mt-1 text-xs text-muted-foreground">把接口 JSON 中的数组读取出来，并按列配置写成 Excel。</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="数据路径 excel.data_path" hint="例如 result.tableData，表示从响应 JSON 的 result.tableData 读取数组。">
          <DraftInput value={excel.data_path || ""} onCommit={(value) => updateExcel({ ...excel, data_path: value })} />
        </Field>
        <Field label="Excel Sheet">
          <DraftInput value={excel.sheet_name || ""} onCommit={(value) => updateExcel({ ...excel, sheet_name: value })} />
        </Field>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-semibold">Excel 列配置</div>
          <Button variant="outline" size="sm" onClick={() => updateExcel({ ...excel, columns: [...columns, { field: "", header: "" }] })}>
            <Plus className="h-4 w-4" />添加列
          </Button>
        </div>
        <div className="space-y-2">
          {columns.map((column, index) => (
            <div key={index} className="grid gap-2 rounded-xl border border-border bg-muted/20 p-2 md:grid-cols-[1fr_1fr_auto_auto_auto]">
              <DraftInput
                placeholder="JSON 字段，如 areaName"
                value={column.field}
                readOnly={isSystemExcelField(column.field)}
                title={isSystemExcelField(column.field) ? "系统下钻字段，字段名固定，只能修改右侧表头" : ""}
                onCommit={(value) => updateColumn(index, { ...column, field: value })}
                className={isSystemExcelField(column.field) ? "bg-muted/70 font-mono text-muted-foreground" : ""}
              />
              <DraftInput placeholder="Excel 表头，如 名称" value={column.header} onCommit={(value) => updateColumn(index, { ...column, header: value })} />
              <Button variant="ghost" size="icon" title="上移" onClick={() => updateExcel({ ...excel, columns: moveAt(columns, index, index - 1) })} disabled={index === 0}>
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" title="下移" onClick={() => updateExcel({ ...excel, columns: moveAt(columns, index, index + 1) })} disabled={index === columns.length - 1}>
                <ArrowDown className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" onClick={() => updateExcel({ ...excel, columns: removeAt(columns, index) })}><Trash2 className="h-4 w-4 text-red-500" /></Button>
            </div>
          ))}
          {!columns.length && <div className="rounded-xl border border-dashed border-border p-3 text-xs text-muted-foreground">还没有列配置。JSON 转 Excel 必须至少配置一列。</div>}
        </div>
      </div>

      {mode === "json_drilldown_to_excel" && <DrilldownConfig item={item} onChange={onChange} />}
    </div>
  );
}

function isSystemExcelField(field: string) {
  return ["__level_name", "__parent_area_id", "__request_area_id"].includes(field);
}

function DrilldownConfig({ item, onChange }: { item: DownloadItem; onChange: (item: DownloadItem) => void }) {
  const drilldown = item.drilldown || defaultDrilldownConfig();
  const updateDrilldown = (nextDrilldown: NonNullable<DownloadItem["drilldown"]>) => onChange({ ...item, drilldown: nextDrilldown });
  const maxRequests = drilldown.max_requests || 1000;
  const maxWorkers = drilldown.max_workers || 6;
  const applyFixedDrilldown = (max_requests: number) => {
    updateDrilldown(defaultDrilldownConfig(max_requests, maxWorkers));
  };

  return (
    <div className="space-y-4 rounded-2xl border border-dashed border-primary/25 bg-primary/5 p-4">
      <div>
        <div className="text-sm font-black">级联下钻配置</div>
        <p className="mt-1 text-xs text-muted-foreground">
          地市作战下钻规则已固定：从响应的 result.tableData 取数据，用 areaCode 替换下一轮请求体里的 areaId，按区县、网格、渠道/门店、人员逐层拉取。
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="下钻数据路径">
          <Input value={FIXED_DRILLDOWN.data_path} readOnly className="bg-muted/70 font-mono text-muted-foreground" />
        </Field>
        <Field label="最大请求数">
          <DraftInput type="number" value={maxRequests} onCommit={(value) => applyFixedDrilldown(Number(value || 0))} />
        </Field>
        <Field label="请求体字段">
          <Input value={FIXED_DRILLDOWN.request_area_field} readOnly className="bg-muted/70 font-mono text-muted-foreground" />
        </Field>
        <Field label="下一层编码字段">
          <Input value={FIXED_DRILLDOWN.next_area_field} readOnly className="bg-muted/70 font-mono text-muted-foreground" />
        </Field>
        <Field label="并发请求数">
          <DraftInput
            type="number"
            value={maxWorkers}
            onCommit={(value) => updateDrilldown({ ...defaultDrilldownConfig(maxRequests, Number(value || 1)) })}
          />
        </Field>
      </div>
      <div className="space-y-2">
        <div className="text-sm font-semibold">层级顺序</div>
        <div className="grid gap-2 md:grid-cols-2">
          {FIXED_DRILLDOWN.levels.map((level, index) => (
            <div key={level} className="rounded-xl border border-border bg-background/80 p-2">
              <Input value={`${index + 1}. ${level}`} readOnly className="bg-muted/70 font-semibold text-muted-foreground" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function JsonTextField({ label, value, error, onChange, onBlur }: { label: string; value: string; error: string; onChange: (value: string) => void; onBlur?: () => void }) {
  return (
    <Field label={label}>
      <Textarea value={value} onChange={(event) => onChange(event.target.value)} onBlur={onBlur} className={`font-mono text-xs ${error ? "border-red-400 focus:ring-red-300" : ""}`} />
      {error && (
        <p className="mt-1 flex items-center gap-1 text-xs font-semibold text-red-500">
          <AlertCircle className="h-3.5 w-3.5" />
          {error}
        </p>
      )}
    </Field>
  );
}

function DraftInput({
  value,
  onCommit,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur"> & {
  value: string | number;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(String(value ?? ""));

  useEffect(() => {
    setDraft(String(value ?? ""));
  }, [value]);

  function commit() {
    if (draft !== String(value ?? "")) {
      onCommit(draft);
    }
  }

  return (
    <Input
      {...props}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          commit();
          event.currentTarget.blur();
        }
      }}
    />
  );
}

function DraftTextarea({
  value,
  onCommit,
  ...props
}: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange" | "onBlur"> & {
  value: string;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");

  useEffect(() => {
    setDraft(value ?? "");
  }, [value]);

  return (
    <Textarea
      {...props}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        if (draft !== (value ?? "")) {
          onCommit(draft);
        }
      }}
    />
  );
}

function CompareTab({ config, onChange }: { config: ReportConfig; onChange: (config: ReportConfig) => void }) {
  const add = () => {
    const firstDownload = config.downloads[0]?.name || "";
    const next: CompareSource = { download_name: firstDownload, sheet_mappings: [{ name: "", new_sheet_name: "", template_sheet_name: "", header_row: 1, ignore_columns: [], key_columns: [] }] };
    onChange({ ...config, compare_sources: [...config.compare_sources, next] });
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <CardTitle>报表比对</CardTitle>
          <CardDescription>把下载结果中的 sheet 映射到模板 sheet，决定后续更新和发送内容。</CardDescription>
        </div>
        <Button className="self-start sm:self-auto" variant="outline" onClick={add}><Plus className="h-4 w-4" />添加比对源</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {config.compare_sources.map((source, sourceIndex) => (
          <div key={sourceIndex} className="rounded-xl border border-border bg-muted/20 p-3 sm:p-4">
            <div className="mb-4 grid gap-4 md:grid-cols-[1fr_auto]">
              <Field label="对应下载标识"><Select value={source.download_name} onChange={(event) => onChange({ ...config, compare_sources: updateAt(config.compare_sources, sourceIndex, { ...source, download_name: event.target.value }) })}>{config.downloads.map((item) => <option key={item.name}>{item.name}</option>)}</Select></Field>
              <Button variant="ghost" size="icon" onClick={() => onChange({ ...config, compare_sources: config.compare_sources.filter((_, index) => index !== sourceIndex) })}><Trash2 className="h-4 w-4 text-red-500" /></Button>
            </div>
            {source.sheet_mappings.map((mapping, mappingIndex) => (
              <div key={mappingIndex} className="mb-3 grid gap-3 rounded-xl bg-background/70 p-3 sm:grid-cols-2 xl:grid-cols-5">
                <DraftInput placeholder="映射备注" value={mapping.name || ""} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, name: value })} />
                <DraftInput placeholder="源 sheet" value={mapping.new_sheet_name} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, new_sheet_name: value })} />
                <DraftInput placeholder="模板 sheet" value={mapping.template_sheet_name} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, template_sheet_name: value })} />
                <DraftInput type="number" placeholder="表头行" value={mapping.header_row || 1} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, header_row: Number(value || 1) })} />
                <DraftInput placeholder="主键列，逗号分隔" value={(mapping.key_columns || []).join(",")} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, key_columns: value.split(",").map((item) => item.trim()).filter(Boolean) })} />
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={() => {
              const next = clone(source);
              next.sheet_mappings.push({ name: "", new_sheet_name: "", template_sheet_name: "", header_row: 1, ignore_columns: [], key_columns: [] });
              onChange({ ...config, compare_sources: updateAt(config.compare_sources, sourceIndex, next) });
            }}>添加 Sheet 映射</Button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function updateMapping(config: ReportConfig, onChange: (config: ReportConfig) => void, sourceIndex: number, mappingIndex: number, mapping: CompareSource["sheet_mappings"][number]) {
  const source = clone(config.compare_sources[sourceIndex]);
  source.sheet_mappings = updateAt(source.sheet_mappings, mappingIndex, mapping);
  onChange({ ...config, compare_sources: updateAt(config.compare_sources, sourceIndex, source) });
}

function SendTab({ config, onChange }: { config: ReportConfig; onChange: (config: ReportConfig) => void }) {
  const send = config.send;
  const updateItem = (index: number, item: SendItem) => onChange({ ...config, send: { ...send, items: updateAt(send.items, index, item) } });
  return (
    <Card>
      <CardHeader>
        <CardTitle>通知配置</CardTitle>
        <CardDescription>配置企业微信机器人、截图 sheet 和文本 sheet。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Workbook 名称"><DraftInput value={send.workbook_name} onCommit={(value) => onChange({ ...config, send: { ...send, workbook_name: value } })} /></Field>
          <Field label="企业微信 Webhook"><DraftInput value={send.webhook_url} onCommit={(value) => onChange({ ...config, send: { ...send, webhook_url: value } })} /></Field>
        </div>
        <div className="space-y-3">
          {send.items.map((item, index) => (
            <div key={index} className="grid gap-3 rounded-xl border border-border bg-muted/20 p-3 sm:grid-cols-[minmax(120px,160px)_1fr] lg:grid-cols-[160px_1fr_180px_auto]">
              <Select value={item.type} onChange={(event) => updateItem(index, { ...item, type: event.target.value as SendItem["type"] })}><option value="image">image</option><option value="text">text</option></Select>
              <DraftInput placeholder="sheet" value={item.sheet} onCommit={(value) => updateItem(index, { ...item, sheet: value })} />
              <Select value={item.text?.mode || "none"} onChange={(event) => updateItem(index, { ...item, text: { mode: event.target.value as "used_range" | "none" } })}><option value="none">none</option><option value="used_range">used_range</option></Select>
              <Button variant="ghost" size="icon" onClick={() => onChange({ ...config, send: { ...send, items: send.items.filter((_, itemIndex) => itemIndex !== index) } })}><Trash2 className="h-4 w-4 text-red-500" /></Button>
            </div>
          ))}
          <Button variant="outline" onClick={() => onChange({ ...config, send: { ...send, items: [...send.items, { type: "image", sheet: "" }] } })}><Plus className="h-4 w-4" />添加发送项</Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AdvancedTab({ config, onChange }: { config: ReportConfig; onChange: (config: ReportConfig) => void }) {
  const update = config.template_update;
  const wait = config.wait_for_change;
  const deployment = config.deployment;
  const updateDeployment = (next: ReportConfig["deployment"]) => {
    const cron = next.cron.trim();
    onChange({ ...config, deployment: { ...next, cron, enabled: Boolean(cron) } });
  };
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>模板更新策略</CardTitle><CardDescription>控制 same/changed 时怎么更新模板和发送。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <Field label="更新引擎"><Select value={update.engine || "hybrid"} onChange={(event) => onChange({ ...config, template_update: { ...update, engine: event.target.value as NonNullable<typeof update.engine> } })}><option value="hybrid">hybrid</option><option value="com_copy">com_copy</option></Select></Field>
          <Field label="changed 判断条件"><Select value={update.update_condition} onChange={(event) => onChange({ ...config, template_update: { ...update, update_condition: event.target.value as typeof update.update_condition } })}><option value="any_changed">any_changed</option><option value="all_changed">all_changed</option></Select></Field>
          <Field label="写入范围"><Select value={update.write_sheets} onChange={(event) => onChange({ ...config, template_update: { ...update, write_sheets: event.target.value as typeof update.write_sheets } })}><option value="changed">changed</option><option value="all_compared">all_compared</option></Select></Field>
          <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={update.send_when_same} onChange={(event) => onChange({ ...config, template_update: { ...update, send_when_same: event.target.checked } })} /> same 时直接发送当前通报</label>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>等待重试</CardTitle><CardDescription>数据未变化时按间隔等待，再重新下载比对。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={wait.enabled} onChange={(event) => onChange({ ...config, wait_for_change: { ...wait, enabled: event.target.checked } })} /> 启用 same 自动重试</label>
          <Field label="重试间隔秒"><DraftInput type="number" value={wait.poll_interval_seconds} onCommit={(value) => onChange({ ...config, wait_for_change: { ...wait, poll_interval_seconds: Number(value || 0) } })} /></Field>
          <Field label="最大等待分钟"><DraftInput type="number" value={wait.max_wait_minutes} onCommit={(value) => onChange({ ...config, wait_for_change: { ...wait, max_wait_minutes: Number(value || 0) } })} /></Field>
        </CardContent>
      </Card>
      <Card className="xl:col-span-2">
        <CardHeader><CardTitle className="flex items-center gap-2"><CalendarClock className="h-5 w-5" />Prefect 定时部署</CardTitle><CardDescription>Cron 为空时只保存部署；Cron 有值时由基础信息里的“启用定时调度”控制是否自动运行。</CardDescription></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Field label="Cron"><DraftInput value={deployment.cron} onCommit={(value) => updateDeployment({ ...deployment, cron: value })} /></Field>
          <Field label="时区"><DraftInput value={deployment.timezone} onCommit={(value) => updateDeployment({ ...deployment, timezone: value })} /></Field>
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, hint, error, className, children }: { label: string; hint?: string; error?: string; className?: string; children: ReactNode }) {
  return (
    <div className={`min-w-0 ${className || ""}`}>
      <Label>{label}</Label>
      <div className="mt-2">{children}</div>
      {hint && <p className="mt-1 text-xs leading-5 text-muted-foreground">{hint}</p>}
      {error && <p className="mt-1 text-xs font-semibold text-red-500">{error}</p>}
    </div>
  );
}


