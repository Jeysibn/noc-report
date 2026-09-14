import type { DashboardSummary, Incident, IncidentStatus } from "@/types/domain";

export interface IncidentQuery {
  /** 0 means all matching records; used by shift report readiness. */
  limit?: number;
  offset?: number;
  shiftId?: string;
  status?: IncidentStatus;
  service?: string;
  environment?: string;
  hasLog?: boolean;
}

export interface IncidentPage {
  items: Incident[];
  total: number;
}

export interface IncidentCreateInput {
  title: string;
  service: string;
  environment: string;
  alertSource?: string;
  triggeredAt: string;
  triggerValue?: string;
  teamsUrl?: string;
  grafanaUrl?: string;
  notes?: string;
}

export interface IncidentTimelineEvent {
  eventType: string;
  label: string;
  occurredAt: string;
  detail?: Record<string, unknown> | null;
}

export interface IncidentUpdateInput {
  title?: string;
  status?: IncidentStatus;
  recoveredAt?: string;
  notes?: string;
}

/** Behind this interface: Mock now, Api later — call sites never change. */
export interface IncidentService {
  list(params: IncidentQuery): Promise<IncidentPage>;
  get(id: string): Promise<Incident | null>;
  create(input: IncidentCreateInput): Promise<Incident>;
  update(id: string, input: IncidentUpdateInput): Promise<Incident>;
  delete(id: string): Promise<void>;
  getDashboardSummary(): Promise<DashboardSummary>;
  getTimeline(id: string): Promise<IncidentTimelineEvent[]>;
}
