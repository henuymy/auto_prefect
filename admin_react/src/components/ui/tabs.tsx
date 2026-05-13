import * as React from "react";
import { cn } from "@/lib/utils";

export function Tabs({ tabs, value, onChange }: { tabs: Array<{ value: string; label: string; icon?: React.ReactNode }>; value: string; onChange: (value: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2 rounded-xl border border-border bg-muted/40 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          onClick={() => onChange(tab.value)}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-muted-foreground transition",
            value === tab.value && "bg-background text-foreground shadow-sm",
          )}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}
