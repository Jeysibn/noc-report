import type { ReactNode } from "react";
import { Card } from "./Card";
import { cn } from "@/lib/cn";

export interface StatCardProps {
  label: string;
  value: string;
  icon: ReactNode;
  delta?: { label: string; direction: "up" | "down" | "flat" };
  progress?: number; // 0-100
}

/** Stat card with icon badge + delta/progress line. */
export function StatCard({ label, value, icon, delta, progress }: StatCardProps) {
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-muted">{label}</p>
          <p className="font-data mt-1 text-2xl font-semibold">{value}</p>
        </div>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-info-tint text-info">
          {icon}
        </div>
      </div>
      {delta && (
        <p
          className={cn(
            "text-xs font-medium",
            delta.direction === "up" && "text-good",
            delta.direction === "down" && "text-critical",
            delta.direction === "flat" && "text-muted",
          )}
        >
          {delta.label}
        </p>
      )}
      {typeof progress === "number" && (
        <div className="h-1.5 w-full rounded-full bg-ground">
          <div
            className="h-1.5 rounded-full bg-accent"
            style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
          />
        </div>
      )}
    </Card>
  );
}
