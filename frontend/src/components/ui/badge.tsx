import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, variant = "default", ...props }: React.HTMLAttributes<HTMLSpanElement> & { variant?: "default" | "success" | "failed" | "running" | "disabled" | "outline" }) {
  const styles = {
    default: "bg-primary/10 text-primary border-primary/20",
    success: "bg-emerald-500/10 text-emerald-600 border-emerald-500/20 dark:text-emerald-300",
    failed: "bg-red-500/10 text-red-600 border-red-500/20 dark:text-red-300",
    running: "bg-sky-500/10 text-sky-600 border-sky-500/20 dark:text-sky-300",
    disabled: "bg-muted text-muted-foreground border-border",
    outline: "bg-transparent text-foreground border-border",
  };
  return <span className={cn("inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold", styles[variant], className)} {...props} />;
}
