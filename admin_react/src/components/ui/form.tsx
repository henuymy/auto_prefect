import * as React from "react";
import { cn } from "@/lib/utils";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn("h-10 w-full min-w-0 rounded-lg border border-input bg-background px-3 py-2 text-sm ring-offset-background transition focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50", props.className)} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn("min-h-24 w-full min-w-0 rounded-lg border border-input bg-background px-3 py-2 text-sm ring-offset-background transition focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50", props.className)} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn("h-10 w-full min-w-0 rounded-lg border border-input bg-background px-3 py-2 text-sm ring-offset-background transition focus:outline-none focus:ring-2 focus:ring-ring", props.className)} />;
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label {...props} className={cn("text-sm font-semibold text-foreground", className)} />;
}
