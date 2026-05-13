import { CheckCircle2, CloudUpload, FileCheck2, Moon, Play, Save, ShieldCheck, Sun, Trash2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Header({
  dark,
  onDarkToggle,
  onSaveDraft,
  onSaveConfig,
  onValidate,
  onTestRun,
  onRealTestRun,
  onDelete,
  canDelete,
  onPublish,
}: {
  dark: boolean;
  onDarkToggle: () => void;
  onSaveDraft: () => void;
  onSaveConfig: () => void;
  onValidate: () => void;
  onTestRun: () => void;
  onRealTestRun: () => void;
  onDelete: () => void;
  canDelete: boolean;
  onPublish: () => void;
}) {
  return (
    <header className="flex h-16 items-center justify-between border-b border-border/70 bg-card/75 px-5 backdrop-blur-xl">
      <div>
        <h1 className="text-xl font-black tracking-tight">基于 Prefect 的自动化任务配置中心</h1>
        <p className="text-xs text-muted-foreground">可视化编辑报表配置 JSON，发布到自动化调度流程</p>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" onClick={onDarkToggle} size="icon" title="切换主题">
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <Button variant="outline" onClick={onSaveDraft}><Save className="h-4 w-4" />保存草稿</Button>
        <Button variant="outline" onClick={onSaveConfig}><FileCheck2 className="h-4 w-4" />保存配置</Button>
        <Button variant="outline" onClick={onValidate}><ShieldCheck className="h-4 w-4" />校验配置</Button>
        <Button variant="secondary" onClick={onTestRun}><Play className="h-4 w-4" />安全测试</Button>
        <Button variant="secondary" onClick={onRealTestRun}><Zap className="h-4 w-4" />真实试跑</Button>
        <Button onClick={onPublish}><CloudUpload className="h-4 w-4" />发布到调度</Button>
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
