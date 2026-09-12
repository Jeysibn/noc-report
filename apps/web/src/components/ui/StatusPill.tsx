import { cn } from "@/lib/cn";

export type Status = "good" | "warning" | "critical" | "info" | "neutral";

const statusClasses: Record<Status, string> = {
  good: "bg-good-tint text-good",
  warning: "bg-warning-tint text-warning",
  critical: "bg-critical-tint text-critical",
  info: "bg-info-tint text-info",
  neutral: "bg-neutral-tint text-muted",
};

const dotClasses: Record<Status, string> = {
  good: "bg-good",
  warning: "bg-warning",
  critical: "bg-critical",
  info: "bg-info",
  neutral: "bg-neutral",
};

export interface StatusPillProps {
  status: Status;
  label: string;
}

/**
 * Status as a colored pill with a dot — never a bare colored word.
 * Per "03 Frontend/UI Phase Plan.md" § Component patterns.
 */
export function StatusPill({ status, label }: StatusPillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        statusClasses[status],
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", dotClasses[status])} />
      {label}
    </span>
  );
}
