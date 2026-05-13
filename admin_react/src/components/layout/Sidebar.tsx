import { Activity, Bell, FileSpreadsheet, GitCompareArrows, MessageSquareText, Settings, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ReportConfig } from "@/types/config";

const menus = [
  { label: "自动化通报任务", icon: Workflow, active: true },
  { label: "Excel 导出任务", icon: FileSpreadsheet, active: false },
  { label: "数据比对任务", icon: GitCompareArrows, active: false },
  { label: "企业微信发送任务", icon: MessageSquareText, active: false },
  { label: "系统设置", icon: Settings, active: false },
];

export function Sidebar({ configs, selectedId, onSelect, onCreate }: { configs: ReportConfig[]; selectedId: string; onSelect: (id: string) => void; onCreate: () => void }) {
  return (
    <aside className="flex h-screen w-80 shrink-0 flex-col border-r border-border/70 bg-card/80 backdrop-blur-xl">
      <div className="flex h-16 items-center gap-3 border-b border-border/70 px-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-glow">
          <Activity className="h-5 w-5" />
        </div>
        <div>
          <div className="text-base font-black">Prefect 配置中心</div>
          <div className="text-xs text-muted-foreground">JSON Visual Console</div>
        </div>
      </div>

      <div className="space-y-1 p-4">
        {menus.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              disabled={!item.active}
              title={item.active ? item.label : "后续扩展，当前未实现"}
              className={cn(
                "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold transition",
                item.active
                  ? "bg-accent text-accent-foreground hover:bg-accent"
                  : "cursor-not-allowed text-muted-foreground/45",
              )}
            >
              <span className="flex items-center gap-3">
              <Icon className="h-4 w-4" />
              {item.label}
              </span>
              {!item.active && <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-bold text-muted-foreground">后续</span>}
            </button>
          );
        })}
      </div>

      <div className="flex items-center justify-between px-4 pb-3 pt-2">
        <div className="text-xs font-bold uppercase tracking-wider text-muted-foreground">配置列表</div>
        <Button size="sm" variant="outline" onClick={onCreate}>新建</Button>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-auto px-4 pb-4">
        {configs.map((config) => (
          <button
            key={config.id}
            onClick={() => onSelect(config.id)}
            className={cn("w-full rounded-2xl border border-border bg-background/70 p-3 text-left transition hover:border-primary/40 hover:shadow-sm", selectedId === config.id && "border-primary/60 bg-primary/5 shadow-glow")}
          >
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="line-clamp-2 text-sm font-bold">{config.name}</div>
              <Badge variant={config.enabled === false ? "disabled" : "success"}>
                {config.enabled === false ? "停用" : "启用"}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground">{config.downloads.length} 个抓取项 · {config.compare_sources.length} 个比对源</div>
          </button>
        ))}
      </div>

      <div className="border-t border-border/70 p-4">
        <div className="flex items-center gap-2 rounded-2xl bg-muted/60 p-3 text-xs text-muted-foreground">
          <Bell className="h-4 w-4 text-primary" />
          当前模块支持配置编辑、保存、测试运行和发布调度。
        </div>
      </div>
    </aside>
  );
}
