import { Activity, Bell, FileSpreadsheet, GitCompareArrows, MessageSquareText, Settings, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ConfigFormTab } from "@/components/config-form/ConfigForm";
import type { ReportConfig } from "@/types/config";

const menus = [
  { label: "自动化通报任务", icon: Workflow, tab: "base" },
  { label: "Excel 导出任务", icon: FileSpreadsheet, tab: "downloads" },
  { label: "数据比对任务", icon: GitCompareArrows, tab: "compare" },
  { label: "企业微信发送任务", icon: MessageSquareText, tab: "send" },
  { label: "系统设置", icon: Settings, tab: "advanced" },
] as const;

export function Sidebar({
  configs,
  selectedId,
  activeTab,
  onTabChange,
  onSelect,
  onCreate,
}: {
  configs: ReportConfig[];
  selectedId: string;
  activeTab: ConfigFormTab;
  onTabChange: (tab: ConfigFormTab) => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="flex max-h-[48dvh] w-full shrink-0 flex-col border-b border-border/70 bg-card/80 backdrop-blur-xl xl:h-dvh xl:max-h-none xl:w-80 xl:border-b-0 xl:border-r">
      <div className="flex min-h-16 items-center gap-3 border-b border-border/70 px-4 py-3 sm:px-5">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-glow">
          <Activity className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-base font-black">Prefect 配置中心</div>
          <div className="text-xs text-muted-foreground">JSON Visual Console</div>
        </div>
      </div>

      <div className="grid gap-2 p-3 sm:grid-cols-2 sm:p-4 xl:block xl:space-y-1">
        {menus.map((item) => {
          const Icon = item.icon;
          const active = activeTab === item.tab;
          return (
            <button
              key={item.label}
              onClick={() => onTabChange(item.tab)}
              title={item.label}
              className={cn(
                "flex w-full min-w-0 items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition",
                active
                  ? "bg-accent text-accent-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
              )}
            >
              <span className="flex min-w-0 items-center gap-3">
              <Icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{item.label}</span>
              </span>
              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-bold", active ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>
                已接入
              </span>
            </button>
          );
        })}
      </div>

      <div className="flex items-center justify-between px-4 pb-3 pt-2">
        <div className="text-xs font-bold uppercase tracking-wider text-muted-foreground">配置列表</div>
        <Button size="sm" variant="outline" onClick={onCreate}>新建</Button>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-auto px-3 pb-3 sm:px-4 sm:pb-4">
        {configs.map((config) => (
          <button
            key={config.id}
            onClick={() => onSelect(config.id)}
            className={cn("w-full rounded-xl border border-border bg-background/70 p-3 text-left transition hover:border-primary/40 hover:shadow-sm", selectedId === config.id && "border-primary/60 bg-primary/5 shadow-glow")}
          >
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="line-clamp-2 text-sm font-bold">{config.name}</div>
              <div className="flex shrink-0 flex-wrap justify-end gap-1">
                {config.source === "draft" && <Badge variant="outline">草稿</Badge>}
                {config.has_draft && config.source !== "draft" && <Badge variant="running">有草稿</Badge>}
                <Badge variant={config.enabled === false ? "disabled" : "success"}>
                  {config.enabled === false ? "调度停用" : "调度启用"}
                </Badge>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">{config.downloads.length} 个抓取项 · {config.compare_sources.length} 个比对源</div>
          </button>
        ))}
      </div>

      <div className="border-t border-border/70 p-3 sm:p-4">
        <div className="flex items-center gap-2 rounded-xl bg-muted/60 p-3 text-xs text-muted-foreground">
          <Bell className="h-4 w-4 shrink-0 text-primary" />
          当前模块支持配置编辑、保存、测试运行和发布调度。
        </div>
      </div>
    </aside>
  );
}
