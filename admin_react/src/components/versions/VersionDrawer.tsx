import { RotateCcw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ConfigVersion } from "@/types/config";

export function VersionDrawer({
  open,
  configName,
  versions,
  onClose,
  onRestore,
}: {
  open: boolean;
  configName: string;
  versions: ConfigVersion[];
  onClose: () => void;
  onRestore: (versionId: string) => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm">
      <aside className="ml-auto flex h-full w-full max-w-2xl flex-col border-l border-border bg-background shadow-2xl">
        <div className="flex items-center justify-between border-b border-border p-5">
          <div>
            <div className="text-lg font-black">配置版本历史</div>
            <div className="mt-1 text-xs text-muted-foreground">{configName}</div>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-5">
          <div className="space-y-2">
            {versions.map((version) => (
              <div key={version.id} className="grid grid-cols-[1fr_120px_auto] items-center gap-3 rounded-xl border border-border bg-card/70 p-3">
                <div className="min-w-0">
                  <div className="font-mono text-sm font-semibold">{version.id}</div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">{version.path}</div>
                </div>
                <Badge variant="outline">{formatSize(version.size)}</Badge>
                <Button variant="outline" size="sm" onClick={() => onRestore(version.id)}>
                  <RotateCcw className="h-4 w-4" />恢复
                </Button>
              </div>
            ))}
            {!versions.length && <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">暂无历史版本。正式保存配置后，会自动备份旧版本。</div>}
          </div>
        </div>
      </aside>
    </div>
  );
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
