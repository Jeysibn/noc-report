export type IncidentStatus = "open" | "investigating" | "recovered";

export interface Incident {
  /** Real backend primary key (UUID) — used for all API calls. */
  id: string;
  shiftId?: string | null;
  /** Human-facing id, e.g. "INC-1042" — what's shown in tables/URLs/labels. */
  displayId: string;
  title: string;
  service: string;
  environment: string;
  status: IncidentStatus;
  triggeredAt: string;
  hasLog: boolean;
  analysisStatus: "not_analyzed" | "queued" | "running" | "retrying" | "completed" | "failed";
}

export type ShiftType = "day" | "swing" | "night";

export interface Shift {
  id: string;
  type: ShiftType;
  operator: string;
  startsAt: string;
  endsAt: string;
  status: "active" | "upcoming" | "ended";
}

export interface DashboardSummary {
  openIncidents: number;
  activeAlerts: number;
  recoveredAlerts: number;
  logsAwaitingAnalysis: number;
  analysesRunning: number;
  reportsGeneratedThisShift: number;
}
