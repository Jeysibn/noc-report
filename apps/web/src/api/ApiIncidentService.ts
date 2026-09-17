import type { DashboardSummary, Incident } from "@/types/domain";
import type {
  IncidentCreateInput,
  IncidentPage,
  IncidentQuery,
  IncidentService,
  IncidentTimelineEvent,
  IncidentUpdateInput,
} from "@/services/incident.service";
import { ApiError, httpRequest } from "@/lib/http";

interface RawIncident {
  id: string;
  display_id: string;
  title: string;
  service: string;
  environment: string;
  status: string;
  triggered_at: string;
  has_log: boolean;
  analysis_status: Incident["analysisStatus"];
  shift_id: string | null;
}

interface RawIncidentPage {
  items: RawIncident[];
  total: number;
}

interface RawTimelineEvent {
  event_type: string;
  label: string;
  occurred_at: string;
  detail: Record<string, unknown> | null;
}

function toIncident(raw: RawIncident): Incident {
  return {
    id: raw.id,
    shiftId: raw.shift_id,
    displayId: raw.display_id,
    title: raw.title,
    service: raw.service,
    environment: raw.environment,
    status: raw.status as Incident["status"],
    triggeredAt: raw.triggered_at,
    hasLog: raw.has_log,
    analysisStatus: raw.analysis_status,
  };
}

/**
 * has_log/analysis_status are computed server-side (see
 * app/api/v1/routers/incidents.py's `_attach_derived`) in one batched
 * query regardless of page size, so list() and get() both return the
 * real state — list() previously hardcoded has_log=false/"not_analyzed"
 * for every row to dodge an N+1 lookup, which made every "Has log"
 * column and readiness table wrong whenever a log actually was attached.
 */
export class ApiIncidentService implements IncidentService {
  async list(params: IncidentQuery): Promise<IncidentPage> {
    const page = await httpRequest<RawIncidentPage>("/api/v1/incidents", {
      query: {
        limit: params.limit,
        offset: params.offset,
        shift_id: params.shiftId,
        status: params.status,
        service: params.service,
        environment: params.environment,
        has_log: params.hasLog,
      },
    });
    return {
      items: page.items.map(toIncident),
      total: page.total,
    };
  }

  async get(id: string): Promise<Incident | null> {
    try {
      const raw = await httpRequest<RawIncident>(`/api/v1/incidents/${id}`);
      return toIncident(raw);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  }

  async create(input: IncidentCreateInput): Promise<Incident> {
    const raw = await httpRequest<RawIncident>("/api/v1/incidents", {
      method: "POST",
      body: {
        title: input.title,
        service: input.service,
        environment: input.environment,
        status: input.status,
        alert_source: input.alertSource,
        triggered_at: input.triggeredAt,
        recovered_at: input.recoveredAt,
        trigger_value: input.triggerValue,
        teams_url: input.teamsUrl,
        grafana_url: input.grafanaUrl,
        notes: input.notes,
      },
    });
    return toIncident(raw);
  }

  async update(id: string, input: IncidentUpdateInput): Promise<Incident> {
    const raw = await httpRequest<RawIncident>(`/api/v1/incidents/${id}`, {
      method: "PATCH",
      body: {
        title: input.title,
        status: input.status,
        recovered_at: input.recoveredAt,
        notes: input.notes,
      },
    });
    return toIncident(raw);
  }

  async delete(id: string): Promise<void> {
    await httpRequest<void>(`/api/v1/incidents/${id}`, { method: "DELETE" });
  }

  async getDashboardSummary(): Promise<DashboardSummary> {
    const raw = await httpRequest<{
      open_incidents: number;
      active_alerts: number;
      recovered_alerts: number;
      logs_awaiting_analysis: number;
      analyses_running: number;
      reports_generated_this_shift: number;
    }>("/api/v1/dashboard/summary");
    return {
      openIncidents: raw.open_incidents,
      activeAlerts: raw.active_alerts,
      recoveredAlerts: raw.recovered_alerts,
      logsAwaitingAnalysis: raw.logs_awaiting_analysis,
      analysesRunning: raw.analyses_running,
      reportsGeneratedThisShift: raw.reports_generated_this_shift,
    };
  }

  async getTimeline(id: string): Promise<IncidentTimelineEvent[]> {
    const raw = await httpRequest<RawTimelineEvent[]>(`/api/v1/incidents/${id}/timeline`);
    return raw.map((e) => ({
      eventType: e.event_type,
      label: e.label,
      occurredAt: e.occurred_at,
      detail: e.detail,
    }));
  }
}
