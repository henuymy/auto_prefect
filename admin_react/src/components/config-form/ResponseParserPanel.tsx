import { useState } from "react";
import { AlertCircle, CheckCircle2, TableProperties } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/form";
import { parseResponseSample, type ResponseParseResult } from "@/lib/responseParser";
import { prettyJson } from "@/lib/utils";
import type { DownloadItem } from "@/types/config";

const DEFAULT_DRILLDOWN = {
  data_path: "result.tableData",
  request_area_field: "areaId",
  next_area_field: "areaCode",
  levels: ["区县", "网格", "渠道/门店", "人员"],
  max_requests: 1000,
};

export function ResponseParserPanel({ item, onApply }: { item: DownloadItem; onApply: (item: DownloadItem) => void }) {
  const [open, setOpen] = useState(false);
  const [rawResponse, setRawResponse] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ResponseParseResult | null>(null);

  const parseAndPreview = () => {
    setError("");
    try {
      setPreview(parseResponseSample(rawResponse));
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : "响应解析失败");
    }
  };

  const applyPreview = () => {
    if (!preview) return;
    const responseMode = item.response_mode === "json_drilldown_to_excel" ? item.response_mode : "json_to_excel";
    const dataPath = preview.kind === "indicator_mapping" ? item.excel?.data_path || "result.tableData" : preview.data_path;
    onApply({
      ...item,
      response_mode: responseMode,
      excel: {
        ...(item.excel || {}),
        data_path: dataPath,
        columns: preview.columns,
      },
      drilldown: responseMode === "json_drilldown_to_excel" ? item.drilldown || DEFAULT_DRILLDOWN : item.drilldown,
    });
  };

  return (
    <div className="mb-4 rounded-2xl border border-dashed border-cyan-500/30 bg-cyan-500/5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span>
          <span className="flex items-center gap-2 text-sm font-black text-foreground">
            <TableProperties className="h-4 w-4 text-cyan-600" />
            响应识别
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            粘贴接口 JSON 响应，自动生成 Excel 列配置。
          </span>
        </span>
        <Badge variant="outline">{open ? "收起" : "展开"}</Badge>
      </button>

      {open && (
        <div className="space-y-4 border-t border-cyan-500/20 p-4">
          <div>
            <Label>响应 JSON</Label>
            <Textarea
              value={rawResponse}
              onChange={(event) => setRawResponse(event.target.value)}
              className="mt-2 min-h-40 font-mono text-xs"
              placeholder='例如 {"result":{"tableData":[{"areaName":"郑州","value":123}]}}'
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={parseAndPreview}>解析响应</Button>
            <Button onClick={applyPreview} disabled={!preview}>确认回填 Excel 配置</Button>
            <span className="text-xs text-muted-foreground">会设置响应模式为 json_to_excel，并生成 data_path 和 columns。</span>
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm font-semibold text-red-500">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}

          {preview && (
            <div className="grid gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 lg:grid-cols-3">
              <div className="lg:col-span-3 flex flex-wrap items-center gap-2 text-sm font-bold text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4" />
                已识别：{preview.kind === "indicator_mapping" ? "指标映射" : preview.data_path} / {preview.rowCount} 行 / {preview.columns.length} 列
              </div>
              <pre className="max-h-48 overflow-auto rounded-lg bg-background/80 p-3 text-xs lg:col-span-1">{prettyJson(preview.columns)}</pre>
              <pre className="max-h-48 overflow-auto rounded-lg bg-background/80 p-3 text-xs lg:col-span-2">{prettyJson(preview.sampleRows)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
