import { useEffect, useRef, useState, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { toast } from "sonner";
import { AlertCircle, ArrowDown, ArrowUp, Bell, CalendarClock, ChevronDown, CloudDownload, Download, FilePlus2, GitCompareArrows, Info, Plus, RefreshCw, Settings, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RequestParserPanel } from "@/components/config-form/RequestParserPanel";
import { ResponseParserPanel } from "@/components/config-form/ResponseParserPanel";
import { Input, Label, Select, Textarea } from "@/components/ui/form";
import { Tabs } from "@/components/ui/tabs";
import { generateStarterTemplate, listTemplates, templateDownloadUrl, uploadTemplate } from "@/lib/api";
import { collectQuickDateFields, DATE_PRESETS, formatCustomDate, updateQuickDateField, type QuickDateField } from "@/lib/quickDate";
import { cn, parseJsonSafe, prettyJson } from "@/lib/utils";
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
  levels: ["网格", "渠道经理", "渠道", "人员"],
  skip_self_row: true,
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

const AUTH_PRESET_DEFAULTS: Record<string, Pick<DownloadItem, "stage" | "headers_from_cookies" | "headers_from_session_storage">> = {
  "报表分析 Ssr-token": {
    stage: "report_analysis",
    headers_from_cookies: { "Ssr-token": "ssr-token" },
  },
  "智慧运营 User-Info": {
    stage: "smart_ops",
    headers_from_session_storage: { "User-Info": "zhyyptInfo.accessToken" },
  },
  "地市平台 Uaptoken": {
    stage: "city_ops",
    headers_from_session_storage: { Uaptoken: "uapToken" },
  },
};

const STARTER_TEMPLATE_STEPS = ["校验配置", "探活/登录", "下载数据", "合并模板", "生成完成"];

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

function normalizeCronList(deployment: ReportConfig["deployment"]) {
  const values = deployment.crons.length ? deployment.crons : [""];
  const normalized = values.map((cron) => String(cron || "").trim());
  return normalized.length ? normalized : [""];
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
  const [generatingStarter, setGeneratingStarter] = useState(false);
  const [starterStep, setStarterStep] = useState(0);
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

  useEffect(() => {
    if (!generatingStarter) return;
    setStarterStep(0);
    const timer = window.setInterval(() => {
      setStarterStep((step) => Math.min(step + 1, STARTER_TEMPLATE_STEPS.length - 2));
    }, 4500);
    return () => window.clearInterval(timer);
  }, [generatingStarter]);

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

  async function handleGenerateStarterTemplate() {
    const confirmed = window.confirm(
      "将真实下载当前配置中的所有抓取项，生成一个包含“通报”空白页和全部下载数据页的 Excel 模板。\n\n会按 Prefect 会话策略处理：先按本次抓取项做 session 探活；探活通过就复用已有会话，探活失败才会重新登录；下载阶段只有明确提示 session 已过期时才强制刷新。\n\n不会发送企业微信，不会提交正式模板，也不会替换当前模板路径。确定继续吗？",
    );
    if (!confirmed) return;
    setStarterStep(0);
    setGeneratingStarter(true);
    try {
      const result = await generateStarterTemplate(config);
      setStarterStep(STARTER_TEMPLATE_STEPS.length - 1);
      await refreshTemplates();
      toast.success("新手模板已生成", {
        description: `已生成 ${result.template_path}，当前模板路径未自动替换`,
        action: {
          label: "下载模板",
          onClick: () => window.open(templateDownloadUrl(result.filename), "_blank", "noopener,noreferrer"),
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      toast.error("新手模板生成失败", { description: message.length > 180 ? `${message.slice(0, 177)}...` : message, duration: 3200 });
    } finally {
      setGeneratingStarter(false);
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
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept=".xlsx,.xlsm,.xls"
              onChange={(event) => void handleTemplateUpload(event.target.files?.[0])}
            />
            <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploading || generatingStarter}>
              <Upload className="h-4 w-4" />
              {uploading ? "上传中" : "上传模板"}
            </Button>
            <Button type="button" variant="secondary" onClick={() => void handleGenerateStarterTemplate()} disabled={uploading || generatingStarter}>
              <FilePlus2 className="h-4 w-4" />
              {generatingStarter ? "生成中" : "生成新手模板"}
            </Button>
            {config.template_path && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => window.open(templateDownloadUrl(config.template_path.split("/").pop() || config.template_path), "_blank", "noopener,noreferrer")}
                title="下载当前模板"
              >
                <Download className="h-4 w-4" />
                下载模板
              </Button>
            )}
          </div>
          {generatingStarter && (
            <StarterTemplateProgress currentStep={starterStep} />
          )}
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

function StarterTemplateProgress({ currentStep }: { currentStep: number }) {
  return (
    <div className="mt-3 rounded-xl border border-sky-200 bg-sky-50/70 p-3 text-xs dark:border-sky-500/30 dark:bg-sky-950/25">
      <div className="flex items-center justify-between gap-3">
        <div className="font-semibold text-sky-700 dark:text-sky-200">正在生成新手模板</div>
        <Badge variant="running">{STARTER_TEMPLATE_STEPS[currentStep]}</Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-5">
        {STARTER_TEMPLATE_STEPS.map((step, index) => {
          const done = index < currentStep;
          const active = index === currentStep;
          return (
            <div
              key={step}
              className={`rounded-lg border px-2 py-2 text-center ${
                done
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-950/30 dark:text-emerald-200"
                  : active
                    ? "border-sky-300 bg-white text-sky-700 shadow-sm dark:border-sky-500/50 dark:bg-sky-950/50 dark:text-sky-100"
                    : "border-border bg-background/60 text-muted-foreground"
              }`}
            >
              {done ? "已完成" : active ? "进行中" : "等待中"} · {step}
            </div>
          );
        })}
      </div>
      <div className="mt-2 text-muted-foreground">
        这个过程会真实登录和下载数据。详细阶段也会写入“查看运行日志”。
      </div>
    </div>
  );
}

function sourceOf(item: DownloadItem) {
  return item.source === "tencent_sheet" ? "tencent_sheet" : "http_api";
}

function defaultHttpDownload(index: number): DownloadItem {
  return {
    source: "http_api",
    name: `抓取项-${index}`,
    stage: "report_analysis",
    auth_preset: "无",
    method: "POST",
    url: "",
    headers: {},
    body_type: "json",
    response_mode: "file",
    data: {},
  };
}

function defaultTencentSheetDownload(index: number): DownloadItem {
  return {
    source: "tencent_sheet",
    name: `腾讯文档-${index}`,
    headers: {},
    file_id: "",
    doc_url: "",
    output_filename: `腾讯文档-${index}.xlsx`,
    sheets: [{ sheet_name: "日报", sheet_id: "", range: "auto", output_sheet_name: "日报" }],
  };
}

function sheetIdFromDocUrl(value?: string) {
  if (!value) return "";
  try {
    return new URL(value).searchParams.get("tab") || "";
  } catch {
    const match = value.match(/[?&]tab=([^&#]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
}

function applyDocUrlToTencentSheet(item: DownloadItem, docUrl: string): DownloadItem {
  const parsedSheetId = sheetIdFromDocUrl(docUrl);
  if (!parsedSheetId) {
    return { ...item, source: "tencent_sheet", doc_url: docUrl };
  }
  const currentSheets = item.sheets?.length ? item.sheets : [{ sheet_name: "日报", sheet_id: "", range: "auto", output_sheet_name: "日报" }];
  const sheets = currentSheets.map((sheet, index) => (
    index === 0 && !sheet.sheet_id ? { ...sheet, sheet_id: parsedSheetId } : sheet
  ));
  return { ...item, source: "tencent_sheet", doc_url: docUrl, sheets };
}

function DownloadsTab({ config, onChange }: { config: ReportConfig; onChange: (config: ReportConfig) => void }) {
  const [activeSource, setActiveSource] = useState<"http_api" | "tencent_sheet">("http_api");
  const entries = config.downloads.map((item, index) => ({ item, index }));
  const httpEntries = entries.filter(({ item }) => sourceOf(item) === "http_api");
  const tencentEntries = entries.filter(({ item }) => sourceOf(item) === "tencent_sheet");
  const activeEntries = activeSource === "http_api" ? httpEntries : tencentEntries;
  const updateDownload = (index: number, next: DownloadItem) => onChange({ ...config, downloads: updateAt(config.downloads, index, next) });
  const deleteDownload = (index: number) => onChange({ ...config, downloads: config.downloads.filter((_, itemIndex) => itemIndex !== index) });
  const addHttp = () => onChange({ ...config, downloads: [...config.downloads, defaultHttpDownload(httpEntries.length + 1)] });
  const addTencent = () => onChange({ ...config, downloads: [...config.downloads, defaultTencentSheetDownload(tencentEntries.length + 1)] });

  return (
    <Card>
      <CardHeader className="space-y-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <CardTitle>数据抓取</CardTitle>
            <CardDescription>按数据源分开配置，避免业务接口请求和腾讯文档范围读取混用。</CardDescription>
          </div>
          <Button className="self-start sm:self-auto" variant="outline" onClick={activeSource === "http_api" ? addHttp : addTencent}>
            <Plus className="h-4 w-4" />
            {activeSource === "http_api" ? "添加接口抓取" : "添加腾讯文档"}
          </Button>
        </div>
        <div className="inline-flex w-full rounded-lg border border-border bg-muted/40 p-1 sm:w-auto">
          <button
            type="button"
            className={`flex-1 rounded-md px-4 py-2 text-sm font-semibold transition sm:flex-none ${activeSource === "http_api" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            onClick={() => setActiveSource("http_api")}
          >
            业务接口 <span className="ml-1 text-xs text-muted-foreground">{httpEntries.length}</span>
          </button>
          <button
            type="button"
            className={`flex-1 rounded-md px-4 py-2 text-sm font-semibold transition sm:flex-none ${activeSource === "tencent_sheet" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            onClick={() => setActiveSource("tencent_sheet")}
          >
            腾讯文档 <span className="ml-1 text-xs text-muted-foreground">{tencentEntries.length}</span>
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {activeSource === "http_api" && <PlaceholderGuide />}
        {activeEntries.map(({ item, index }, viewIndex) => (
          activeSource === "http_api" ? (
            <HttpDownloadCard
              key={`${item.name}-${index}`}
              item={item}
              index={viewIndex}
              onChange={(next) => updateDownload(index, next)}
              onDelete={() => deleteDownload(index)}
              deleteDisabled={config.downloads.length <= 1}
            />
          ) : (
            <TencentSheetDownloadCard
              key={`${item.name}-${index}`}
              item={item}
              index={viewIndex}
              onChange={(next) => updateDownload(index, next)}
              onDelete={() => deleteDownload(index)}
              deleteDisabled={config.downloads.length <= 1}
            />
          )
        ))}
        {!activeEntries.length && (
          <div className="rounded-xl border border-dashed border-border bg-muted/20 p-6 text-center text-sm text-muted-foreground">
            {activeSource === "http_api" ? "还没有业务接口抓取项。" : "还没有腾讯文档抓取项。"}
          </div>
        )}
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

function HttpDownloadCard({ item, index, onChange, onDelete, deleteDisabled }: { item: DownloadItem; index: number; onChange: (item: DownloadItem) => void; onDelete: () => void; deleteDisabled?: boolean }) {
  const [open, setOpen] = useState(index === 0);
  const [dateOpen, setDateOpen] = useState(false);
  const [headersText, setHeadersText] = useState(prettyJson(item.headers || {}));
  const [dataText, setDataText] = useState(prettyJson(item.data || {}));
  const [authText, setAuthText] = useState(prettyJson(getAuthMapping(item)));
  const headersError = headersText.trim() ? parseJsonSafe(headersText).ok ? "" : "请求头 JSON 格式错误" : "";
  const dataError = dataText.trim() ? parseJsonSafe(dataText).ok ? "" : "请求体 JSON 格式错误" : "";
  const authError = authText.trim() ? parseJsonSafe(authText).ok ? "" : "动态认证 JSON 格式错误" : "";
  const hasJsonError = Boolean(headersError || dataError || authError);
  const quickDateFields = collectQuickDateFields(item);

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
      Object.assign(next, AUTH_PRESET_DEFAULTS[authPreset]);
    } else if (authPreset === "智慧运营 User-Info") {
      Object.assign(next, AUTH_PRESET_DEFAULTS[authPreset]);
    } else if (authPreset === "地市平台 Uaptoken") {
      Object.assign(next, AUTH_PRESET_DEFAULTS[authPreset]);
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
            <Badge variant="outline">{item.stage || "stage 未填"}</Badge>
            <ChevronDown className={`h-4 w-4 text-muted-foreground transition ${open ? "rotate-180" : ""}`} />
          </div>
          <h3 className="mt-2 text-base font-black">{item.name || "未命名抓取项"}</h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{item.url || "尚未填写 URL"}</p>
        </button>
        <div className="flex shrink-0 items-center gap-1">
          {quickDateFields.length > 0 && (
            <Button variant={dateOpen ? "default" : "outline"} size="sm" onClick={() => setDateOpen((value) => !value)} title="快速修改请求日期">
              <CalendarClock className="h-4 w-4" />
              日期 {quickDateFields.length}
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={onDelete} disabled={deleteDisabled}><Trash2 className="h-4 w-4 text-red-500" /></Button>
        </div>
      </div>
      {dateOpen && quickDateFields.length > 0 && (
        <QuickDateEditor item={item} fields={quickDateFields} onChange={onChange} />
      )}
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
        <Field label="Cookie Stage"><Select value={item.stage || "report_analysis"} onChange={(event) => onChange({ ...item, stage: event.target.value })}><option>report_analysis</option><option>smart_ops</option><option>city_ops</option><option>data_market</option></Select></Field>
        <Field label="响应模式 response_mode"><Select value={item.response_mode || ""} onChange={(event) => onChange(ensureDrilldown({ ...item, response_mode: event.target.value as DownloadItem["response_mode"] }))}><option value="" disabled>请选择响应模式</option><option value="file">file</option><option value="json_to_excel">json_to_excel</option><option value="json_drilldown_to_excel">json_drilldown_to_excel</option></Select></Field>
        <Field label="请求方法 method"><Select value={item.method || ""} onChange={(event) => onChange({ ...item, method: event.target.value as DownloadItem["method"] })}><option value="" disabled>请选择请求方法</option><option>POST</option><option>GET</option><option>PUT</option><option>PATCH</option><option>DELETE</option></Select></Field>
        <Field label="载体类型 body_type"><Select value={item.body_type || ""} onChange={(event) => onChange({ ...item, body_type: event.target.value as DownloadItem["body_type"] })}><option value="" disabled>请选择载体类型</option><option>json</option><option>form</option><option>raw</option></Select></Field>
        <Field label="认证预设 auth_preset">
          <Select value={normalizeAuthPreset(item.auth_preset)} onChange={(event) => applyAuthPreset(event.target.value)}>
            {AUTH_PRESET_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </Select>
          <p className="mt-1 text-xs text-muted-foreground">{getAuthTargetLabel(item)}；选择预设会自动切换 Cookie Stage。</p>
        </Field>
        <Field label="下载 URL" hint="可以写占位符，例如 queryDate=${today_yyyymmdd}" className="lg:col-span-3"><DraftInput value={item.url || ""} onCommit={(value) => onChange({ ...item, url: value })} /></Field>
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

function QuickDateEditor({ item, fields, onChange }: { item: DownloadItem; fields: QuickDateField[]; onChange: (item: DownloadItem) => void }) {
  const applyAll = (day: "today" | "yesterday") => {
    let next = item;
    fields.forEach((field) => {
      const compact = /_yyyymmdd\}/i.test(field.value) || /^\d{8}$/.test(field.value);
      next = updateQuickDateField(next, field, compact ? `\${${day}_yyyymmdd}` : `\${${day}}`);
    });
    onChange(next);
  };

  return (
    <div className="mb-4 rounded-xl border border-sky-200 bg-sky-50/70 p-3 dark:border-sky-500/30 dark:bg-sky-950/25">
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-black text-sky-950 dark:text-sky-100">快速修改日期</div>
          <p className="mt-0.5 text-xs text-muted-foreground">自动识别 URL、请求体、请求头和 Raw Body 中的日期字段。</p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => applyAll("today")}>全部今天</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => applyAll("yesterday")}>全部昨天</Button>
        </div>
      </div>
      <div className="space-y-2">
        {fields.map((field) => (
          <div key={field.id} className="grid gap-2 rounded-lg border border-sky-100 bg-background/85 p-2.5 lg:grid-cols-[minmax(0,1fr)_220px_170px] lg:items-center dark:border-sky-500/20">
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold" title={field.label}>{field.label}</div>
              <code className="mt-1 block truncate text-[11px] text-muted-foreground" title={field.value}>{field.value}</code>
            </div>
            <Select
              aria-label={`${field.label} 日期预设`}
              value={DATE_PRESETS.some((preset) => preset.value === field.value) ? field.value : ""}
              onChange={(event) => event.target.value && onChange(updateQuickDateField(item, field, event.target.value))}
            >
              <option value="">选择动态日期…</option>
              {DATE_PRESETS.map((preset) => <option key={preset.value} value={preset.value}>{preset.label}</option>)}
            </Select>
            <Input
              type="date"
              aria-label={`${field.label} 自定义日期`}
              onChange={(event) => event.target.value && onChange(updateQuickDateField(item, field, formatCustomDate(event.target.value, field.value)))}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function TencentSheetDownloadCard({ item, index, onChange, onDelete, deleteDisabled }: { item: DownloadItem; index: number; onChange: (item: DownloadItem) => void; onDelete: () => void; deleteDisabled?: boolean }) {
  const [open, setOpen] = useState(index === 0);
  const sheets = item.sheets?.length ? item.sheets : [{ sheet_name: "日报", sheet_id: "", range: "auto", output_sheet_name: "日报" }];
  const updateSheets = (nextSheets: NonNullable<DownloadItem["sheets"]>) => onChange({ ...item, source: "tencent_sheet", sheets: nextSheets });
  const subtitle = item.doc_url || (item.file_id ? `file_id: ${item.file_id}` : "尚未填写腾讯文档完整链接");

  return (
    <div className="rounded-xl border border-border bg-muted/20 p-3 sm:p-4">
      <div className="mb-4 flex items-start justify-between gap-4">
        <button type="button" onClick={() => setOpen((value) => !value)} className="min-w-0 flex-1 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">#{index + 1}</Badge>
            <Badge>腾讯文档</Badge>
            <Badge variant="outline">按范围读取</Badge>
            <Badge variant="outline">{sheets.length} 个 Sheet</Badge>
            <ChevronDown className={`h-4 w-4 text-muted-foreground transition ${open ? "rotate-180" : ""}`} />
          </div>
          <h3 className="mt-2 text-base font-black">{item.name || "未命名腾讯文档"}</h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{subtitle}</p>
        </button>
        <Button variant="ghost" size="icon" onClick={onDelete} disabled={deleteDisabled}><Trash2 className="h-4 w-4 text-red-500" /></Button>
      </div>
      {open && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Field label="下载标识 name"><DraftInput value={item.name} onCommit={(value) => onChange({ ...item, source: "tencent_sheet", name: value })} /></Field>
            <Field label="输出文件名"><DraftInput value={item.output_filename || ""} onCommit={(value) => onChange({ ...item, source: "tencent_sheet", output_filename: value })} /></Field>
            <Field label="腾讯文档完整链接 doc_url" hint="建议填写带 tab 的完整链接，例如 https://docs.qq.com/sheet/DY1h4R1Rmd0FwWFhF?tab=000002；首个 Sheet 的 sheet_id 会自动带出。">
              <DraftInput value={item.doc_url || ""} onCommit={(value) => onChange(applyDocUrlToTencentSheet(item, value))} />
            </Field>
            <Field label="高级：file_id（可选）" hint="通常留空；只有已拿到 OpenAPI 内部 file_id 时才填写。">
              <DraftInput value={item.file_id || ""} onCommit={(value) => onChange({ ...item, source: "tencent_sheet", file_id: value })} />
            </Field>
          </div>
          <div className="rounded-xl border border-border bg-background/70 p-3">
            <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-black">Sheet 范围配置</div>
                <p className="mt-1 text-xs text-muted-foreground">运行时会自动分块读取，避开导出接口每日 9 次限制。</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => updateSheets([...sheets, { sheet_name: "日报", sheet_id: "", range: "auto", output_sheet_name: "日报" }])}>
                <Plus className="h-4 w-4" />添加 Sheet
              </Button>
            </div>
            <div className="space-y-2">
              <div className="hidden grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 px-2 text-xs font-semibold text-muted-foreground lg:grid">
                <div>Sheet 名称</div>
                <div>sheet_id</div>
                <div>读取范围</div>
                <div>输出 Sheet</div>
                <div />
              </div>
              {sheets.map((sheet, sheetIndex) => (
                <div key={sheetIndex} className="grid gap-2 rounded-lg border border-border bg-muted/10 p-2 lg:grid-cols-[1fr_1fr_1fr_1fr_auto]">
                  <DraftInput placeholder="Sheet 名称" value={sheet.sheet_name || ""} onCommit={(value) => updateSheets(updateAt(sheets, sheetIndex, { ...sheet, sheet_name: value }))} />
                  <DraftInput placeholder="sheet_id" value={sheet.sheet_id || ""} onCommit={(value) => updateSheets(updateAt(sheets, sheetIndex, { ...sheet, sheet_id: value }))} />
                  <DraftInput placeholder="auto 或 A1:Z1000" value={sheet.range || ""} onCommit={(value) => updateSheets(updateAt(sheets, sheetIndex, { ...sheet, range: value || "auto" }))} />
                  <DraftInput placeholder="输出 Sheet" value={sheet.output_sheet_name || ""} onCommit={(value) => updateSheets(updateAt(sheets, sheetIndex, { ...sheet, output_sheet_name: value }))} />
                  <div className="flex items-center justify-end gap-1">
                    <Button variant="ghost" size="icon" title="复制" onClick={() => updateSheets([...sheets.slice(0, sheetIndex + 1), { ...sheet }, ...sheets.slice(sheetIndex + 1)])}>
                      <FilePlus2 className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" title="删除" onClick={() => updateSheets(removeAt(sheets, sheetIndex))} disabled={sheets.length <= 1}>
                      <Trash2 className="h-4 w-4 text-red-500" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
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
            <div key={index} className="grid gap-2 rounded-xl border border-border bg-muted/20 p-2 md:grid-cols-[1fr_1fr_120px_auto_auto_auto]">
              <DraftInput
                placeholder="JSON 字段，如 areaName"
                value={column.field}
                readOnly={isSystemExcelField(column.field)}
                title={isSystemExcelField(column.field) ? "系统下钻字段，字段名固定，只能修改右侧表头" : ""}
                onCommit={(value) => updateColumn(index, { ...column, field: value })}
                className={isSystemExcelField(column.field) ? "bg-muted/70 font-mono text-muted-foreground" : ""}
              />
              <DraftInput placeholder="Excel 表头，如 名称" value={column.header} onCommit={(value) => updateColumn(index, { ...column, header: value })} />
              <Select value={column.type || ""} onChange={(event) => updateColumn(index, { ...column, type: event.target.value as ExcelColumn["type"] })}>
                <option value="">文本/原样</option>
                <option value="number">数字</option>
              </Select>
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
  const levels = drilldown.levels?.length ? drilldown.levels : FIXED_DRILLDOWN.levels;
  const applyFixedDrilldown = (max_requests: number) => {
    updateDrilldown(defaultDrilldownConfig(max_requests, maxWorkers));
  };
  const updateLevel = (index: number, value: string) => {
    updateDrilldown({ ...drilldown, levels: updateAt(levels, index, value || `层级${index + 1}`) });
  };

  return (
    <div className="space-y-4 rounded-2xl border border-dashed border-primary/25 bg-primary/5 p-4">
        <div>
          <div className="text-sm font-black">级联下钻配置</div>
          <p className="mt-1 text-xs text-muted-foreground">
          地市作战下钻规则默认从响应的 result.tableData 取数据，用 areaCode 替换下一轮请求体里的 areaId，层级顺序可按实际接口调整。
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
        <Field label="过滤自身汇总行">
          <label className="flex min-h-10 items-center gap-3 rounded-lg border border-input bg-background px-3 text-sm">
            <input
              type="checkbox"
              checked={drilldown.skip_self_row !== false}
              onChange={(event) => updateDrilldown({ ...drilldown, skip_self_row: event.target.checked })}
            />
            <span>跳过 areaCode 等于当前请求 areaId 的汇总行</span>
          </label>
        </Field>
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-semibold">层级顺序</div>
          <Button variant="outline" size="sm" onClick={() => updateDrilldown({ ...drilldown, levels: [...levels, `层级${levels.length + 1}`] })}>
            <Plus className="h-4 w-4" />添加层级
          </Button>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {levels.map((level, index) => (
            <div key={`${level}-${index}`} className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-2 rounded-xl border border-border bg-background/80 p-2">
              <DraftInput value={level} onCommit={(value) => updateLevel(index, value)} />
              <Button variant="ghost" size="icon" title="上移" onClick={() => updateDrilldown({ ...drilldown, levels: moveAt(levels, index, index - 1) })} disabled={index === 0}>
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" title="下移" onClick={() => updateDrilldown({ ...drilldown, levels: moveAt(levels, index, index + 1) })} disabled={index === levels.length - 1}>
                <ArrowDown className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" title="删除" onClick={() => updateDrilldown({ ...drilldown, levels: removeAt(levels, index) })} disabled={levels.length <= 1}>
                <Trash2 className="h-4 w-4 text-red-500" />
              </Button>
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
    const next: CompareSource = { download_name: firstDownload, engine: "openpyxl", max_workers: 4, sheet_mappings: [{ name: "", new_sheet_name: "", template_sheet_name: "", header_row: 1, ignore_columns: [], key_columns: [] }] };
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
            <div className="mb-4 grid gap-4 md:grid-cols-[1fr_160px_160px_auto]">
              <Field label="对应下载标识"><Select value={source.download_name} onChange={(event) => onChange({ ...config, compare_sources: updateAt(config.compare_sources, sourceIndex, { ...source, download_name: event.target.value }) })}>{config.downloads.map((item) => <option key={item.name}>{item.name}</option>)}</Select></Field>
              <Field label="比对引擎"><Select value={source.engine || "openpyxl"} onChange={(event) => onChange({ ...config, compare_sources: updateAt(config.compare_sources, sourceIndex, { ...source, engine: event.target.value as NonNullable<CompareSource["engine"]> }) })}><option value="openpyxl">openpyxl</option><option value="com">com</option></Select></Field>
              <Field label="并发 Sheet 数"><DraftInput type="number" value={source.max_workers || 4} onCommit={(value) => onChange({ ...config, compare_sources: updateAt(config.compare_sources, sourceIndex, { ...source, max_workers: Number(value || 1) }) })} /></Field>
              <Button variant="ghost" size="icon" onClick={() => onChange({ ...config, compare_sources: config.compare_sources.filter((_, index) => index !== sourceIndex) })}><Trash2 className="h-4 w-4 text-red-500" /></Button>
            </div>
            {source.sheet_mappings.map((mapping, mappingIndex) => (
              <div key={mappingIndex} className="mb-3 grid gap-3 rounded-xl bg-background/70 p-3 sm:grid-cols-2 xl:grid-cols-5">
                <DraftInput placeholder="映射备注" value={mapping.name || ""} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, name: value })} />
                <DraftInput placeholder="源 sheet" value={mapping.new_sheet_name} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, new_sheet_name: value })} />
                <DraftInput placeholder="模板 sheet" value={mapping.template_sheet_name} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, template_sheet_name: value })} />
                <DraftInput type="number" placeholder="表头行，0=无表头" value={mapping.header_row ?? 1} onCommit={(value) => updateMapping(config, onChange, sourceIndex, mappingIndex, { ...mapping, header_row: value === "" ? 1 : Number(value) })} />
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
  const crons = normalizeCronList(deployment);
  const sameMode: "retry" | "send" = update.send_when_same ? "send" : "retry";
  const setSameMode = (mode: "retry" | "send") => {
    onChange({
      ...config,
      template_update: { ...update, send_when_same: mode === "send" },
      wait_for_change: { ...wait, enabled: mode === "retry" },
    });
  };
  const updateDeployment = (next: ReportConfig["deployment"], nextCrons = crons) => {
    const cleanCrons = nextCrons.map((cron) => cron.trim()).filter(Boolean);
    onChange({ ...config, deployment: { ...next, crons: cleanCrons, enabled: cleanCrons.length > 0 } });
  };
  const updateCron = (index: number, value: string) => {
    updateDeployment(deployment, updateAt(crons, index, value));
  };
  const removeCron = (index: number) => {
    updateDeployment(deployment, removeAt(crons, index));
  };
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>模板更新策略</CardTitle><CardDescription>控制 same/changed 时怎么更新模板和发送。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <Field label="更新引擎"><Select value={update.engine || "hybrid"} onChange={(event) => onChange({ ...config, template_update: { ...update, engine: event.target.value as NonNullable<typeof update.engine> } })}><option value="hybrid">hybrid</option><option value="com_copy">com_copy</option></Select></Field>
          <Field label="changed 判断条件"><Select value={update.update_condition} onChange={(event) => onChange({ ...config, template_update: { ...update, update_condition: event.target.value as typeof update.update_condition } })}><option value="any_changed">any_changed</option><option value="all_changed">all_changed</option></Select></Field>
          <Field label="写入范围"><Select value={update.write_sheets} onChange={(event) => onChange({ ...config, template_update: { ...update, write_sheets: event.target.value as typeof update.write_sheets } })}><option value="changed">changed</option><option value="all_compared">all_compared</option></Select></Field>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>same 时处理方式</CardTitle><CardDescription>控制数据一致或下载数据区为空时怎么继续。</CardDescription></CardHeader>
        <CardContent className="space-y-5">
          <div className="rounded-xl border border-border bg-muted/30 p-1">
            <div className="grid grid-cols-2 gap-1">
              <button
                type="button"
                className={cn(
                  "flex h-10 items-center justify-center gap-2 rounded-lg border text-sm font-semibold transition",
                  sameMode === "retry" ? "border-sky-300 bg-sky-100 text-sky-950 shadow-sm" : "border-transparent text-muted-foreground hover:bg-background/70 hover:text-foreground",
                )}
                onClick={() => setSameMode("retry")}
              >
                <RefreshCw className="h-4 w-4" />
                等待重试
              </button>
              <button
                type="button"
                className={cn(
                  "flex h-10 items-center justify-center gap-2 rounded-lg border text-sm font-semibold transition",
                  sameMode === "send" ? "border-sky-300 bg-sky-100 text-sky-950 shadow-sm" : "border-transparent text-muted-foreground hover:bg-background/70 hover:text-foreground",
                )}
                onClick={() => setSameMode("send")}
              >
                <Bell className="h-4 w-4" />
                直接发送
              </button>
            </div>
          </div>
          <div className="rounded-lg border border-dashed border-sky-200 bg-sky-50/70 px-3 py-2 text-sm text-sky-950">
            {sameMode === "retry"
              ? "数据一致或下载数据区为空时，按间隔重新下载并比对。"
              : "即使数据一致，也继续生成并发送当前通报。"}
          </div>
          {sameMode === "retry" && (
            <div className="grid gap-4 rounded-xl bg-muted/30 p-3 sm:grid-cols-2">
              <Field label="重试间隔秒"><DraftInput type="number" value={wait.poll_interval_seconds} onCommit={(value) => onChange({ ...config, wait_for_change: { ...wait, poll_interval_seconds: Number(value || 0) } })} /></Field>
              <Field label="最大等待分钟"><DraftInput type="number" value={wait.max_wait_minutes} onCommit={(value) => onChange({ ...config, wait_for_change: { ...wait, max_wait_minutes: Number(value || 0) } })} /></Field>
            </div>
          )}
        </CardContent>
      </Card>
      <Card className="xl:col-span-2">
        <CardHeader><CardTitle className="flex items-center gap-2"><CalendarClock className="h-5 w-5" />Prefect 定时部署</CardTitle><CardDescription>可配置多条 Cron；有任意 Cron 时由基础信息里的“启用定时调度”控制是否自动运行。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <Field label="时区"><DraftInput value={deployment.timezone} onCommit={(value) => updateDeployment({ ...deployment, timezone: value })} /></Field>
          <div className="space-y-3">
            {crons.map((cron, index) => (
              <div key={index} className="grid gap-3 md:grid-cols-[1fr_auto]">
                <Field label={`Cron ${index + 1}`}>
                  <DraftInput value={cron} onCommit={(value) => updateCron(index, value)} />
                </Field>
                <div className="flex items-end">
                  <Button variant="ghost" size="icon" onClick={() => removeCron(index)} disabled={crons.length === 1 && !cron}>
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <Button variant="outline" onClick={() => updateDeployment(deployment, [...crons, ""])}>
            <Plus className="h-4 w-4" />
            添加 Cron
          </Button>
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


