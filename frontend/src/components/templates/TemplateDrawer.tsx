import { useEffect, useState } from "react";
import { Download, Trash2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deleteTemplate, listTemplates, templateDownloadUrl, uploadTemplate } from "@/lib/api";

type TemplateItem = { filename: string; path: string; size: number; modifiedAt: number };

export function TemplateDrawer({ open, onClose, onDeleted }: { open: boolean; onClose: () => void; onDeleted: (path: string) => void }) {
  const [items, setItems] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(false);
  async function refresh() { setLoading(true); try { setItems(await listTemplates()); } finally { setLoading(false); } }
  useEffect(() => { if (open) void refresh(); }, [open]);
  if (!open) return null;
  return <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm">
    <aside className="ml-auto flex h-full w-full max-w-2xl flex-col border-l border-border bg-background shadow-2xl">
      <header className="flex items-center gap-3 border-b border-border p-4"><div className="flex-1"><div className="font-black">模板管理</div><div className="text-xs text-muted-foreground">上传、下载和删除 Excel 模板</div></div>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-sm font-semibold"><Upload className="h-4 w-4" />上传<input className="hidden" type="file" accept=".xlsx,.xlsm,.xls" onChange={async (event) => { const file = event.target.files?.[0]; if (file) { await uploadTemplate(file); await refresh(); } event.target.value = ""; }} /></label>
        <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
      </header>
      <div className="min-h-0 flex-1 space-y-2 overflow-auto p-4">
        {items.map((item) => <div key={item.path} className="flex items-center gap-3 rounded-xl border border-border p-3"><div className="min-w-0 flex-1"><div className="truncate font-semibold">{item.filename}</div><div className="text-xs text-muted-foreground">{(item.size / 1024).toFixed(1)} KB</div></div>
          <a href={templateDownloadUrl(item.filename)}><Button variant="outline" size="icon"><Download className="h-4 w-4" /></Button></a>
          <Button variant="ghost" size="icon" onClick={async () => { if (!window.confirm(`确定删除模板 ${item.filename} 吗？`)) return; await deleteTemplate(item.filename); onDeleted(item.path); await refresh(); }}><Trash2 className="h-4 w-4" /></Button>
        </div>)}
        {!loading && !items.length && <div className="p-8 text-center text-sm text-muted-foreground">暂无模板</div>}
      </div>
    </aside>
  </div>;
}
