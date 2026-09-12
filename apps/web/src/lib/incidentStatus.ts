import type { Incident } from "@/types/domain";
import type { Status } from "@/components/ui/StatusPill";

export const incidentStatusMap: Record<Incident["status"], { status: Status; label: string }> = {
  open: { status: "critical", label: "Open" },
  investigating: { status: "warning", label: "Investigating" },
  recovered: { status: "good", label: "Recovered" },
};

export function formatTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
