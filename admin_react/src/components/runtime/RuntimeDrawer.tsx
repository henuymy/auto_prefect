import { ArrowLeft, Folder, RefreshCw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { RuntimeCleanupPreview, RuntimeEntry } from "@/types/config";

type Props = {
  open: boolean; currentPath: string; items: RuntimeEntry[]; loading: boolean;
  onClose: () => void; onOpenPath: (path: string) => void; onBack: () => void;
  onRefresh: () => void; onDelete: (path: string) => void; cleanupDays: number;
  onCleanupDaysChange: (days: number) => void; cleanupPreview: RuntimeCleanupPreview | null;
  onPreviewCleanup: () => void; onRunCleanup: () => void;
};

export function RuntimeDrawer(props: Props) {
  if (!props.open) return null;
  return <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm">
    <aside className="ml-auto flex h-full w-full max-w-3xl flex-col border-l border-border bg-background shadow-2xl">
      <header className="flex items-center gap-2 border-b border-border p-4">
        <Button variant="ghost" size="icon" onClick={props.onBack} disabled={!props.currentPath}><ArrowLeft className="h-4 w-4" /></Button>
        <div className="min-w-0 flex-1"><div className="font-black">Runtime 文件</div><div className="truncate text-xs text-muted-foreground">runtime/{props.currentPath}</div></div>
        <Button variant="ghost" size="icon" onClick={props.onRefresh}><RefreshCw className={`h-4 w-4 ${props.loading ? "animate-spin" : ""}`} /></Button>
        <Button variant="ghost" size="icon" onClick={props.onClose}><X className="h-4 w-4" /></Button>
      </header>
      <div className="flex items-center gap-2 border-b border-border p-4">
        <input className="w-20 rounded-md border border-border bg-background px-2 py-1 text-sm" type="number" min={1} value={props.cleanupDays} onChange={(event) => props.onCleanupDaysChange(Number(event.target.value) || 1)} />
        <span className="text-sm text-muted-foreground">天前</span>
        <Button variant="outline" size="sm" onClick={props.onPreviewCleanup}>预览清理</Button>
        {props.cleanupPreview && <Button variant="destructive" size="sm" disabled={!props.cleanupPreview.count} onClick={props.onRunCleanup}>清理 {props.cleanupPreview.count} 项</Button>}
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-auto p-4">
        {props.items.map((item) => <div key={item.path} className="flex items-center gap-3 rounded-xl border border-border p-3">
          <Folder className="h-4 w-4 text-muted-foreground" />
          <button className="min-w-0 flex-1 truncate text-left text-sm font-semibold" onClick={() => item.type === "directory" && props.onOpenPath(item.path)}>{item.name}</button>
          <span className="text-xs text-muted-foreground">{item.size == null ? "" : formatSize(item.size)}</span>
          <Button variant="ghost" size="icon" onClick={() => props.onDelete(item.path)}><Trash2 className="h-4 w-4" /></Button>
        </div>)}
        {!props.loading && !props.items.length && <div className="p-8 text-center text-sm text-muted-foreground">此目录为空</div>}
      </div>
    </aside>
  </div>;
}

function formatSize(size: number) { return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`; }
