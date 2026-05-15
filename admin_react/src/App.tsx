import { useEffect, useMemo, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { toast } from "sonner";
import { FileSpreadsheet, FolderClock, GitBranch, History } from "lucide-react";
import { ConfigForm } from "@/components/config-form/ConfigForm";
import type { ConfigFormTab } from "@/components/config-form/ConfigForm";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { JsonPanel } from "@/components/json-panel/JsonPanel";
import { RunLogDrawer } from "@/components/run-log/RunLogDrawer";
import { RuntimeDrawer } from "@/components/runtime/RuntimeDrawer";
import { TemplateDrawer } from "@/components/templates/TemplateDrawer";
import { VersionDrawer } from "@/components/versions/VersionDrawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { createConfig, deleteConfig, deleteRuntime, getConfig, getSystemStatus, listConfigVersions, listConfigs, listRunLogs, listRuntime, previewRuntimeCleanup, publishConfig, realTestRunConfig, restoreConfigVersion, runRuntimeCleanup, saveDraftConfig, testRunConfig, updateConfig, validateConfig } from "@/lib/api";
import { uid } from "@/lib/utils";
import { validateReportConfig } from "@/schemas/reportConfigSchema";
import type { ConfigVersion, ReportConfig, RunLog, RuntimeCleanupPreview, RuntimeEntry, SystemStatus, ValidationIssue } from "@/types/config";

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => (typeof window === "undefined" ? false : window.matchMedia(query).matches));

  useEffect(() => {
    const media = window.matchMedia(query);
    const updateMatches = () => setMatches(media.matches);
    updateMatches();
    media.addEventListener("change", updateMatches);
    return () => media.removeEventListener("change", updateMatches);
  }, [query]);

  return matches;
}

function emptyConfig(): ReportConfig {
  return {
    id: uid("cfg"),
    name: "新建通报配置",
    template_path: "templates/新建通报模板.xlsx",
    enabled: true,
    description: "",
    downloads: [
      {
        name: "默认抓取项",
        stage: "report_analysis",
        auth_preset: "无",
        method: "POST",
        url: "",
        headers: {},
        body_type: "json",
        response_mode: "file",
        data: {},
      },
    ],
    compare_sources: [],
    send: {
      webhook_url: "",
      workbook_name: "新建通报配置",
      items: [{ type: "image", sheet: "通报" }],
    },
    template_update: {
      update_condition: "any_changed",
      write_sheets: "all_compared",
      send_when_same: true,
    },
    wait_for_change: {
      enabled: false,
      poll_interval_seconds: 300,
      max_wait_minutes: 180,
    },
    deployment: {
      enabled: false,
      cron: "",
      timezone: "Asia/Shanghai",
    },
    lastRun: "disabled",
  };
}

function isTemporaryConfigId(id: string) {
  return id.startsWith("cfg_");
}

type JsonPath = Array<string | number>;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function findFirstDiffPath(before: unknown, after: unknown, path: JsonPath = []): JsonPath {
  if (Object.is(before, after)) return path;

  if (Array.isArray(before) && Array.isArray(after)) {
    const length = Math.max(before.length, after.length);
    for (let index = 0; index < length; index += 1) {
      if (!Object.is(before[index], after[index])) {
        return findFirstDiffPath(before[index], after[index], [...path, index]);
      }
    }
    return path;
  }

  if (isPlainObject(before) && isPlainObject(after)) {
    const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
    for (const key of keys) {
      if (!Object.is(before[key], after[key])) {
        return findFirstDiffPath(before[key], after[key], [...path, key]);
      }
    }
    return path;
  }

  return path;
}

function toastDescription(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return message.length > 180 ? `${message.slice(0, 177)}...` : message;
}

export default function App() {
  const [configs, setConfigs] = useState<ReportConfig[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [config, setConfig] = useState<ReportConfig | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [logs, setLogs] = useState<RunLog[]>([]);
  const [logsOpen, setLogsOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [runtimePath, setRuntimePath] = useState("");
  const [runtimeItems, setRuntimeItems] = useState<RuntimeEntry[]>([]);
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [cleanupDays, setCleanupDays] = useState(7);
  const [cleanupPreview, setCleanupPreview] = useState<RuntimeCleanupPreview | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState<ConfigVersion[]>([]);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [jsonFocusPath, setJsonFocusPath] = useState<JsonPath>([]);
  const [activeConfigTab, setActiveConfigTab] = useState<ConfigFormTab>("base");
  const [dark, setDark] = useState(false);
  const wideLayout = useMediaQuery("(min-width: 1280px)");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    if (!logsOpen) return;

    let cancelled = false;
    const refreshLogs = async () => {
      const nextLogs = await listRunLogs();
      if (!cancelled) {
        setLogs(nextLogs);
      }
    };

    void refreshLogs();
    const timer = window.setInterval(() => void refreshLogs(), 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [logsOpen]);

  const liveIssues = useMemo(() => (config ? validateReportConfig(config) : []), [config]);
  const persistedConfigs = useMemo(() => configs.filter((item) => !isTemporaryConfigId(item.id)), [configs]);
  const isPersistedConfig = useMemo(() => {
    if (!config) return false;
    if (isTemporaryConfigId(config.id)) return false;
    return persistedConfigs.some((item) => item.id === config.id);
  }, [config, persistedConfigs]);
  const sidebarConfigs = useMemo(() => {
    if (!config || isPersistedConfig) return persistedConfigs;
    return [config, ...persistedConfigs];
  }, [config, persistedConfigs, isPersistedConfig]);

  async function bootstrap() {
    try {
      const [loadedConfigs, loadedLogs, loadedStatus] = await Promise.all([listConfigs(), listRunLogs(), getSystemStatus()]);
      setConfigs(loadedConfigs);
      setLogs(loadedLogs);
      setSystemStatus(loadedStatus);
      if (loadedConfigs[0]) {
        setSelectedId(loadedConfigs[0].id);
        const first = await getConfig(loadedConfigs[0].id);
        setConfig(first);
        setIssues(validateReportConfig(first));
      } else {
        createLocalConfig();
      }
    } catch (error) {
      toast.error("后端服务未连接", { description: "请先启动 backend：python -m uvicorn backend.app:app --reload --port 8000" });
      createLocalConfig();
    }
  }

  async function refreshStatus() {
    try {
      setSystemStatus(await getSystemStatus());
      toast.success("系统状态已刷新");
    } catch (error) {
      toast.error("系统状态读取失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function selectConfig(id: string) {
    if (isTemporaryConfigId(id)) {
      if (config?.id === id) return;
      toast.info("这是未保存的新建配置", { description: "请先保存配置后再从列表切换。" });
      return;
    }
    setSelectedId(id);
    const loaded = await getConfig(id);
    setConfig(loaded);
    setIssues(validateReportConfig(loaded));
  }

  function createLocalConfig() {
    if (config && !isPersistedConfig) {
      toast.info("当前已经是新建配置", { description: "先填写或保存当前配置，再新建下一份。" });
      return;
    }
    const next = emptyConfig();
    setSelectedId(next.id);
    setConfig(next);
    setIssues(validateReportConfig(next));
    toast.success("已新建通报配置", { description: "填写完成后点击“保存配置”写入 config/reports。" });
  }

  function updateConfigFromForm(next: ReportConfig) {
    if (config) {
      setJsonFocusPath(findFirstDiffPath(config, next));
    }
    setConfig(next);
    setIssues(validateReportConfig(next));
  }

  function blockIfInvalid(actionName: string) {
    if (!config) return true;
    const nextIssues = validateReportConfig(config);
    setIssues(nextIssues);
    if (!nextIssues.length) return false;
    toast.error(`${actionName}前请先修复配置`, {
      description: `${nextIssues[0].path}: ${nextIssues[0].message}`,
    });
    return true;
  }

  async function saveDraft() {
    if (!config) return;
    try {
      const saved = await saveDraftConfig(config.id, config);
      setConfig(saved);
      setLogs(await listRunLogs());
      toast.success("草稿已保存", { description: `runtime/drafts/${saved.name}.json` });
    } catch (error) {
      toast.error("保存草稿失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function saveConfig() {
    if (!config) return;
    if (blockIfInvalid("保存配置")) return;
    try {
      const saved = isPersistedConfig ? await updateConfig(config.id, config) : await createConfig(config);
      setSelectedId(saved.id);
      setConfig(saved);
      setConfigs(await listConfigs());
      setLogs(await listRunLogs());
      toast.success("配置已保存", { description: `config/reports/${saved.name}.json` });
    } catch (error) {
      toast.error("保存配置失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function validate() {
    if (!config) return;
    try {
      const nextIssues = await validateConfig(config);
      const nextLogs = await listRunLogs();
      setIssues(nextIssues);
      setLogs(nextLogs);
      if (nextIssues.length) {
        toast.error("配置校验失败", { description: `发现 ${nextIssues.length} 个问题` });
      } else {
        toast.success("配置校验通过");
      }
    } catch (error) {
      toast.error("配置校验失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function testRun() {
    if (!config) return;
    if (blockIfInvalid("安全测试")) return;
    try {
      await testRunConfig(config);
      setLogs(await listRunLogs());
      setLogsOpen(true);
      toast.info("已创建测试运行请求");
    } catch (error) {
      toast.error("测试运行失败，详情见运行日志", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function realTestRun() {
    if (!config) return;
    if (blockIfInvalid("真实试跑")) return;
    if (!window.confirm("真实试跑会真实下载、比对、生成截图并发送企业微信，但不会提交正式模板。确定继续吗？")) return;
    try {
      await realTestRunConfig(config);
      setLogs(await listRunLogs());
      setLogsOpen(true);
      toast.success("真实试跑完成");
    } catch (error) {
      toast.error("真实试跑失败，详情见运行日志", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function deleteCurrentConfig() {
    if (!config) return;
    if (!isPersistedConfig || isTemporaryConfigId(config.id)) {
      toast.info("当前是未保存的新建配置", { description: "不会删除任何文件；如果不需要，直接切换到其他配置即可。" });
      return;
    }
    if (!window.confirm(`确定删除配置「${config.name}」吗？该操作会同步删除 config/reports、config/tasks 和 runtime/drafts 中的同名配置文件。`)) return;
    try {
      const result = await deleteConfig(config.id);
      const nextConfigs = (await listConfigs()).filter((item) => !isTemporaryConfigId(item.id));
      setConfigs(nextConfigs);
      setLogs(await listRunLogs());
      if (nextConfigs[0]) {
        setSelectedId(nextConfigs[0].id);
        const next = await getConfig(nextConfigs[0].id);
        setConfig(next);
        setIssues(validateReportConfig(next));
      } else {
        createLocalConfig();
      }
      toast.success("配置已删除", { description: `同步清理 ${result.deleted?.length || 0} 个配置文件` });
    } catch (error) {
      toast.error("删除配置失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function publish() {
    if (!config) return;
    if (blockIfInvalid("发布到调度")) return;
    try {
      const status = await getSystemStatus();
      setSystemStatus(status);
      if (!status.prefect.ok) {
        toast.error("Prefect 未连接，不能发布到调度", { description: status.prefect.api_url });
        return;
      }
      const nextIssues = await validateConfig(config);
      setIssues(nextIssues);
      if (nextIssues.length) {
        toast.error("发布前请先修复校验错误");
        return;
      }
      await publishConfig(config);
      setConfigs(await listConfigs());
      setLogs(await listRunLogs());
      setLogsOpen(true);
      toast.success("已提交发布到调度");
    } catch (error) {
      toast.error("发布失败，详情见运行日志", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function openRuntime(path = runtimePath) {
    setRuntimeLoading(true);
    try {
      const result = await listRuntime(path);
      setRuntimePath(result.current.path === "." ? "" : result.current.path);
      setRuntimeItems(result.items);
      setCleanupPreview(null);
      setRuntimeOpen(true);
    } catch (error) {
      toast.error("读取 runtime 失败", { description: toastDescription(error), duration: 2400 });
    } finally {
      setRuntimeLoading(false);
    }
  }

  async function removeRuntime(path: string) {
    if (!window.confirm(`确定删除 runtime/${path} 吗？`)) return;
    try {
      await deleteRuntime(path);
      await openRuntime(runtimePath);
      setLogs(await listRunLogs());
      toast.success("runtime 文件已删除");
    } catch (error) {
      toast.error("删除失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function previewCleanup() {
    try {
      const preview = await previewRuntimeCleanup(cleanupDays, runtimePath);
      setCleanupPreview(preview);
      toast.info("清理预览已生成", { description: `将清理 ${preview.count} 项` });
    } catch (error) {
      toast.error("清理预览失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function executeCleanup() {
    if (!cleanupPreview?.count) return;
    if (!window.confirm(`确定清理 ${cleanupPreview.count} 项 runtime 文件吗？`)) return;
    try {
      const result = await runRuntimeCleanup(cleanupDays, runtimePath);
      setCleanupPreview(result);
      await openRuntime(runtimePath);
      setLogs(await listRunLogs());
      toast.success("Runtime 清理完成", { description: `已删除 ${result.deleted?.length || 0} 项` });
    } catch (error) {
      toast.error("Runtime 清理失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function openVersions() {
    if (!config) return;
    try {
      setVersions(await listConfigVersions(config.id));
      setVersionsOpen(true);
    } catch (error) {
      toast.error("读取版本历史失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  async function restoreVersion(versionId: string) {
    if (!config) return;
    if (!window.confirm(`确定恢复版本 ${versionId} 吗？当前配置会先备份再恢复。`)) return;
    try {
      const restored = await restoreConfigVersion(config.id, versionId);
      setConfig(restored);
      setConfigs(await listConfigs());
      setLogs(await listRunLogs());
      setVersions(await listConfigVersions(restored.id));
      toast.success("配置版本已恢复");
    } catch (error) {
      toast.error("恢复版本失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  function runtimeBack() {
    if (!runtimePath) return;
    const parts = runtimePath.split(/[\\/]/).filter(Boolean);
    parts.pop();
    void openRuntime(parts.join("/"));
  }

  async function openRunLogs() {
    try {
      setLogs(await listRunLogs());
      setLogsOpen(true);
    } catch (error) {
      toast.error("读取运行日志失败", { description: toastDescription(error), duration: 2400 });
    }
  }

  function handleTemplateDeleted(path: string) {
    if (config?.template_path === path) {
      setConfig({ ...config, template_path: "" });
    }
  }

  if (!config) {
    return <div className="app-shell flex h-screen items-center justify-center text-muted-foreground">正在加载配置中心...</div>;
  }

  const jsonPanel = (
    <JsonPanel
      config={config}
      issues={issues.length ? issues : liveIssues}
      focusPath={jsonFocusPath}
      onJsonApply={(next) => {
        setConfig(next);
        setIssues(validateReportConfig(next));
      }}
    />
  );

  return (
    <div className="app-shell flex min-h-dvh flex-col overflow-x-hidden xl:h-dvh xl:flex-row xl:overflow-hidden">
      <Sidebar
        configs={sidebarConfigs}
        selectedId={selectedId}
        activeTab={activeConfigTab}
        onTabChange={setActiveConfigTab}
        onSelect={selectConfig}
        onCreate={createLocalConfig}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <Header
          dark={dark}
          onDarkToggle={() => setDark((value) => !value)}
          onSaveDraft={saveDraft}
          onSaveConfig={saveConfig}
          onValidate={validate}
          onTestRun={testRun}
          onRealTestRun={realTestRun}
          onDelete={deleteCurrentConfig}
          canDelete={isPersistedConfig}
          onPublish={publish}
        />
        <div className="min-h-0 flex-1 p-3 sm:p-4 lg:p-5">
          {wideLayout ? (
            <PanelGroup direction="horizontal" className="h-full rounded-2xl border border-border/70 bg-background/40 p-2 backdrop-blur-xl">
              <Panel defaultSize={58} minSize={38}>
                <div className="h-full overflow-auto p-3">
                  <ConfigForm config={config} tab={activeConfigTab} onTabChange={setActiveConfigTab} onChange={updateConfigFromForm} />
                </div>
              </Panel>
              <PanelResizeHandle className="mx-2 w-1 rounded-full bg-border transition hover:bg-primary" />
              <Panel defaultSize={42} minSize={30}>
                <div className="h-full p-3">{jsonPanel}</div>
              </Panel>
            </PanelGroup>
          ) : (
            <div className="space-y-4 rounded-2xl border border-border/70 bg-background/40 p-3 backdrop-blur-xl">
              <ConfigForm config={config} tab={activeConfigTab} onTabChange={setActiveConfigTab} onChange={updateConfigFromForm} />
              <div className="h-[70dvh] min-h-[520px]">{jsonPanel}</div>
            </div>
          )}
        </div>
        <div className="flex min-h-12 flex-col gap-3 border-t border-border/70 bg-card/70 px-3 py-3 text-xs text-muted-foreground backdrop-blur-xl lg:flex-row lg:items-center lg:justify-between lg:px-5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span>真实 API 模式：配置写入 config/reports，草稿写入 runtime/drafts。</span>
            {systemStatus && (
              <>
                <Badge variant={systemStatus.backend.ok ? "success" : "failed"}>Backend {systemStatus.backend.ok ? "OK" : "Down"}</Badge>
                <Badge variant={systemStatus.prefect.ok ? "success" : "failed"} title={systemStatus.prefect.message}>
                  Prefect {systemStatus.prefect.ok ? "OK" : "未连接"}
                </Badge>
              </>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => void refreshStatus()}>刷新状态</Button>
            <Button variant="ghost" size="sm" onClick={() => void openVersions()}><GitBranch className="h-4 w-4" />版本历史</Button>
            <Button variant="ghost" size="sm" onClick={() => setTemplatesOpen(true)}><FileSpreadsheet className="h-4 w-4" />模板管理</Button>
            <Button variant="ghost" size="sm" onClick={() => void openRuntime("")}><FolderClock className="h-4 w-4" />Runtime 文件</Button>
            <Button variant="ghost" size="sm" onClick={() => void openRunLogs()}><History className="h-4 w-4" />查看运行日志</Button>
          </div>
        </div>
      </main>
      <RunLogDrawer open={logsOpen} logs={logs} onClose={() => setLogsOpen(false)} />
      <TemplateDrawer
        open={templatesOpen}
        onClose={() => setTemplatesOpen(false)}
        onDeleted={handleTemplateDeleted}
      />
      <RuntimeDrawer
        open={runtimeOpen}
        currentPath={runtimePath}
        items={runtimeItems}
        loading={runtimeLoading}
        onClose={() => setRuntimeOpen(false)}
        onOpenPath={(path) => void openRuntime(path)}
        onBack={runtimeBack}
        onRefresh={() => void openRuntime(runtimePath)}
        onDelete={removeRuntime}
        cleanupDays={cleanupDays}
        onCleanupDaysChange={setCleanupDays}
        cleanupPreview={cleanupPreview}
        onPreviewCleanup={() => void previewCleanup()}
        onRunCleanup={() => void executeCleanup()}
      />
      <VersionDrawer
        open={versionsOpen}
        configName={config.name}
        versions={versions}
        onClose={() => setVersionsOpen(false)}
        onRestore={(versionId) => void restoreVersion(versionId)}
      />
    </div>
  );
}
