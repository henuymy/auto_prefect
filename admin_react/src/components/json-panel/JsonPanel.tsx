import { createRef, useEffect, useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import { AlertTriangle, CheckCircle2, Code2, Copy, FileJson2, GitPullRequestDraft } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import { parseJsonSafe, prettyJson } from "@/lib/utils";
import type { ReportConfig, ValidationIssue } from "@/types/config";

type JsonPath = Array<string | number>;

export function JsonPanel({
  config,
  issues,
  focusPath,
  onJsonApply,
}: {
  config: ReportConfig;
  issues: ValidationIssue[];
  focusPath: JsonPath;
  onJsonApply: (config: ReportConfig) => void;
}) {
  const [tab, setTab] = useState("preview");
  const [source, setSource] = useState(prettyJson(config));
  const [sourceError, setSourceError] = useState("");
  const jsonText = useMemo(() => prettyJson(config), [config]);

  const tabs = [
    { value: "preview", label: "JSON 预览", icon: <FileJson2 className="h-4 w-4" /> },
    { value: "source", label: "源码编辑", icon: <Code2 className="h-4 w-4" /> },
    { value: "validate", label: "校验结果", icon: <CheckCircle2 className="h-4 w-4" /> },
    { value: "diff", label: "Diff 对比", icon: <GitPullRequestDraft className="h-4 w-4" /> },
  ];

  const copy = async () => navigator.clipboard.writeText(tab === "source" ? source : jsonText);

  useEffect(() => {
    if (tab !== "source") {
      setSource(jsonText);
      setSourceError("");
    }
  }, [jsonText, tab]);

  useEffect(() => {
    if (tab !== "source") return;
    const parsed = parseJsonSafe<ReportConfig>(source);
    if (!parsed.ok) {
      setSourceError("JSON 格式错误，修正后会自动同步到表单。");
      return;
    }

    setSourceError("");
    if (prettyJson(parsed.value) === jsonText) return;

    const timer = window.setTimeout(() => {
      onJsonApply(parsed.value);
    }, 600);

    return () => window.clearTimeout(timer);
  }, [jsonText, onJsonApply, source, tab]);

  return (
    <Card className="devtools-panel flex h-full min-h-0 flex-col overflow-hidden">
      <div className="border-b border-border/70 p-3 sm:p-4">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="text-base font-black">开发者 JSON 面板</div>
            <div className="text-xs text-muted-foreground">表单和源码双向查看，第一版使用 mock 校验。</div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={issues.length ? "failed" : "success"}>{issues.length ? `${issues.length} errors` : "valid"}</Badge>
            <Button variant="outline" size="icon" onClick={copy}><Copy className="h-4 w-4" /></Button>
          </div>
        </div>
        <Tabs tabs={tabs} value={tab} onChange={(value) => { setTab(value); if (value === "source") setSource(jsonText); }} />
      </div>
      <div className="min-h-0 flex-1 overflow-hidden p-3 sm:p-4">
        {tab === "preview" && <JsonPreview value={config} focusPath={focusPath} />}
        {tab === "source" && (
          <div className="flex h-full min-h-0 flex-col gap-3">
            <div className="rounded-xl border border-sky-500/20 bg-sky-500/10 px-3 py-2 text-xs text-muted-foreground">
              源码编辑会在 JSON 合法时自动同步到左侧表单；如果格式错误，会先停在这里等待修正。
            </div>
            <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-border">
              <Editor
                height="100%"
                language="json"
                theme={document.documentElement.classList.contains("dark") ? "vs-dark" : "light"}
                value={source}
                onChange={(value) => setSource(value || "")}
                options={{ minimap: { enabled: false }, fontSize: 13, formatOnPaste: true, formatOnType: true, tabSize: 2, wordWrap: "on" }}
              />
            </div>
            {sourceError && (
              <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-500">
                {sourceError}
              </div>
            )}
          </div>
        )}
        {tab === "validate" && <ValidationView issues={issues} />}
        {tab === "diff" && <DiffPlaceholder />}
      </div>
    </Card>
  );
}

function JsonPreview({ value, focusPath }: { value: unknown; focusPath: JsonPath }) {
  const text = prettyJson(value);
  const lines = text.split("\n");
  const focusLine = useMemo(() => findLineForPath(lines, focusPath), [lines, focusPath]);
  const refs = useMemo(() => lines.map(() => createRef<HTMLDivElement>()), [text]);

  useEffect(() => {
    if (focusLine < 0) return;
    refs[focusLine]?.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusLine, refs]);

  return (
    <div className="h-full overflow-auto rounded-xl border border-border bg-slate-950 p-4 text-sm text-slate-100 shadow-inner dark:bg-black/40">
      <pre className="font-mono leading-6">
        <code>
          {lines.map((line, index) => (
            <div
              key={`${index}-${line}`}
              ref={refs[index]}
              className={`whitespace-pre-wrap break-words rounded px-1 transition-colors ${index === focusLine ? "bg-sky-400/25 text-sky-100 ring-1 ring-sky-300/40" : ""}`}
            >
              {line || " "}
            </div>
          ))}
        </code>
      </pre>
    </div>
  );
}

function findLineForPath(lines: string[], path: JsonPath) {
  if (!path.length) return -1;

  let cursor = 0;
  let matched = -1;

  for (const segment of path) {
    if (typeof segment === "number") {
      cursor = findArrayItemLine(lines, cursor, segment);
      if (cursor >= 0) matched = cursor;
      continue;
    }

    const target = `"${segment}":`;
    const next = lines.findIndex((line, index) => index >= cursor && line.includes(target));
    if (next < 0) {
      break;
    }
    matched = next;
    cursor = next + 1;
  }

  if (matched >= 0) return matched;

  const fallbackKey = [...path].reverse().find((segment): segment is string => typeof segment === "string");
  if (!fallbackKey) return -1;
  return lines.findIndex((line) => line.includes(`"${fallbackKey}":`));
}

function findArrayItemLine(lines: string[], start: number, itemIndex: number) {
  let seen = -1;
  for (let index = start; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (trimmed === "{" || trimmed === "{") {
      seen += 1;
      if (seen === itemIndex) return index;
    }
  }
  return start;
}

function ValidationView({ issues }: { issues: ValidationIssue[] }) {
  if (!issues.length) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-8 text-center">
        <div>
          <CheckCircle2 className="mx-auto mb-4 h-12 w-12 text-emerald-500" />
          <div className="text-lg font-black text-emerald-600 dark:text-emerald-300">配置校验通过</div>
          <p className="mt-2 text-sm text-muted-foreground">当前 JSON 符合第一版 Schema，可以保存、测试运行或发布。</p>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-3 overflow-auto">
      {issues.map((issue, index) => (
        <div key={`${issue.path}-${index}`} className="rounded-xl border border-red-500/20 bg-red-500/10 p-4">
          <div className="flex items-center gap-2 text-sm font-black text-red-600 dark:text-red-300"><AlertTriangle className="h-4 w-4" />{issue.path}</div>
          <div className="mt-1 text-sm text-muted-foreground">{issue.message}</div>
        </div>
      ))}
    </div>
  );
}

function DiffPlaceholder() {
  return (
    <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 p-8 text-center">
      <div>
        <GitPullRequestDraft className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
        <div className="text-lg font-black">Diff 对比待接入</div>
        <p className="mt-2 max-w-sm text-sm text-muted-foreground">后续后端保存历史版本后，这里可以对比当前草稿和已发布版本。</p>
      </div>
    </div>
  );
}
