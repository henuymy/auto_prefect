import { CheckCircle2, CloudUpload, CopyPlus, FileCheck2, Loader2, Moon, Play, Save, ShieldCheck, Sun, Trash2, Zap } from "lucide-react";
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
}) {
  return (
    <header className="flex min-h-16 flex-col gap-3 border-b border-border/70 bg-card/75 px-3 py-3 backdrop-blur-xl lg:flex-row lg:items-center lg:justify-between lg:px-5">
      <div className="min-w-0">
        <h1 className="text-lg font-black tracking-tight sm:text-xl">基于 Prefect 的自动化任务配置中心</h1>
        <p className="text-xs text-muted-foreground">可视化编辑报表配置 JSON，发布到自动化调度流程</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
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
        <Button onClick={onPublish} disabled={publishing}>
          {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudUpload className="h-4 w-4" />}
          {publishing ? "发布中" : "发布到调度"}
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
