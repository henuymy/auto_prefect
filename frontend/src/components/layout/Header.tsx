import { CheckCircle2, CloudOff, CloudUpload, CopyPlus, FileCheck2, Loader2, MoreHorizontal, Moon, Play, Save, ShieldCheck, Sun, Trash2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Header({
  dark,
  onDarkToggle,
  onSaveDraft,
  onSaveConfig,
  onCopyConfig,
  canCopy,
  onValidate,
  onTestRun,
  testing,
  onRealTestRun,
  realTesting,
  onDelete,
  canDelete,
  onPublish,
  publishing,
  onDeleteDeployment,
  deletingDeployment,
  canDeleteDeployment,
}: {
  dark: boolean;
  onDarkToggle: () => void;
  onSaveDraft: () => void;
  onSaveConfig: () => void;
  onCopyConfig: () => void;
  canCopy: boolean;
  onValidate: () => void;
  onTestRun: () => void;
  testing: boolean;
  onRealTestRun: () => void;
  realTesting: boolean;
  onDelete: () => void;
  canDelete: boolean;
  onPublish: () => void;
  publishing: boolean;
  onDeleteDeployment: () => void;
  deletingDeployment: boolean;
  canDeleteDeployment: boolean;
}) {
  return (
    <header className="flex min-h-16 flex-col gap-3 border-b border-border/70 bg-card/75 px-3 py-3 backdrop-blur-xl lg:flex-row lg:items-center lg:justify-between lg:px-5">
      <div className="min-w-0">
        <h1 className="text-lg font-black tracking-tight sm:text-xl">自动化任务配置中心</h1>
        <p className="text-xs text-muted-foreground">可视化编辑报表配置 JSON，发布到自动化调度流程</p>
      </div>
      <div className="flex w-full flex-wrap items-center gap-2 lg:hidden">
        <Button variant="outline" onClick={onSaveDraft} className="h-11 flex-1 px-2"><Save className="h-4 w-4" />保存草稿</Button>
        <Button variant="outline" onClick={onSaveConfig} className="h-11 flex-1 px-2"><FileCheck2 className="h-4 w-4" />保存配置</Button>
        <Button onClick={onPublish} disabled={publishing || deletingDeployment} className="h-11 flex-1 px-2" title="同名更新原调度；改名会新建调度">
          {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudUpload className="h-4 w-4" />}
          {publishing ? "发布中" : "发布调度"}
        </Button>
        <Button variant="outline" onClick={onDarkToggle} size="icon" className="h-11 w-11 shrink-0" title="切换主题">
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <details className="w-full rounded-lg border border-border bg-background/60 p-1 open:bg-background">
          <summary className="flex h-10 cursor-pointer list-none items-center justify-center gap-2 rounded-md px-3 text-sm font-semibold text-muted-foreground hover:bg-accent hover:text-accent-foreground [&::-webkit-details-marker]:hidden">
            <MoreHorizontal className="h-4 w-4" />更多操作
          </summary>
          <div className="grid grid-cols-2 gap-1 border-t border-border/70 pt-1">
            <Button variant="ghost" onClick={onCopyConfig} disabled={!canCopy} className="h-11 min-w-0 justify-center px-2" title={canCopy ? "复制当前配置并另存为新配置" : "请先保存当前配置再复制"}>
              <CopyPlus className="h-4 w-4" />复制配置
            </Button>
            <Button variant="ghost" onClick={onValidate} className="h-11 min-w-0 justify-center px-2"><ShieldCheck className="h-4 w-4" />校验配置</Button>
            <Button variant="ghost" onClick={onTestRun} disabled={testing || realTesting} className="h-11 min-w-0 justify-center px-2">
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {testing ? "测试中" : "安全测试"}
            </Button>
            <Button variant="ghost" onClick={onRealTestRun} disabled={testing || realTesting} className="h-11 min-w-0 justify-center px-2">
              {realTesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
              {realTesting ? "试跑中" : "真实试跑"}
            </Button>
            <Button variant="ghost" onClick={onDeleteDeployment} disabled={!canDeleteDeployment || publishing || deletingDeployment} className="h-11 min-w-0 justify-center px-2 text-destructive hover:text-destructive" title={canDeleteDeployment ? "删除当前配置名称对应的 Prefect Deployment" : "请先保存并发布当前配置"}>
              {deletingDeployment ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudOff className="h-4 w-4" />}
              {deletingDeployment ? "删除中" : "删除部署"}
            </Button>
            <Button variant="ghost" onClick={onDelete} disabled={!canDelete} className="h-11 min-w-0 justify-center px-2 text-destructive hover:text-destructive" title={canDelete ? "删除当前已保存配置" : "新建配置尚未保存，不能删除文件"}>
              <Trash2 className="h-4 w-4" />删除配置
            </Button>
          </div>
        </details>
      </div>
      <div className="hidden flex-wrap items-center gap-2 lg:flex">
        <Button variant="outline" onClick={onDarkToggle} size="icon" title="切换主题">
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <Button variant="outline" onClick={onSaveDraft}><Save className="h-4 w-4" />保存草稿</Button>
        <Button variant="outline" onClick={onSaveConfig}><FileCheck2 className="h-4 w-4" />保存配置</Button>
        <Button variant="outline" onClick={onCopyConfig} disabled={!canCopy} title={canCopy ? "复制当前配置并另存为新配置" : "请先保存当前配置再复制"}>
          <CopyPlus className="h-4 w-4" />复制配置
        </Button>
        <Button variant="outline" onClick={onValidate}><ShieldCheck className="h-4 w-4" />校验配置</Button>
        <Button variant="secondary" onClick={onTestRun} disabled={testing || realTesting}>
          {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {testing ? "测试中" : "安全测试"}
        </Button>
        <Button variant="secondary" onClick={onRealTestRun} disabled={testing || realTesting}>
          {realTesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
          {realTesting ? "试跑中" : "真实试跑"}
        </Button>
        <Button onClick={onPublish} disabled={publishing || deletingDeployment} title="同名更新原调度；改名会新建调度">
          {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudUpload className="h-4 w-4" />}
          {publishing ? "发布中" : "发布到调度"}
        </Button>
        <Button
          variant="destructive"
          onClick={onDeleteDeployment}
          disabled={!canDeleteDeployment || publishing || deletingDeployment}
          title={canDeleteDeployment ? "删除当前配置名称对应的 Prefect Deployment" : "请先保存并发布当前配置"}
        >
          {deletingDeployment ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudOff className="h-4 w-4" />}
          {deletingDeployment ? "删除中" : "删除部署"}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDelete}
          disabled={!canDelete}
          title={canDelete ? "删除当前已保存配置" : "新建配置尚未保存，不能删除文件"}
        >
          <Trash2 className="h-4 w-4 text-red-500" />
        </Button>
        <Button variant="ghost" size="icon" title="当前配置已加载"><CheckCircle2 className="h-4 w-4 text-emerald-500" /></Button>
      </div>
    </header>
  );
}
