import type { Incident } from "@/types/domain";
import type {
  IncidentCreateInput,
  IncidentPage,
  IncidentQuery,
  IncidentService,
  IncidentTimelineEvent,
  IncidentUpdateInput,
} from "@/services/incident.service";
import { mockIncidents } from "./fixtures/incidents";

export class MockIncidentService implements IncidentService {
  async list({ limit, offset = 0, shiftId, status, service, environment, hasLog }: IncidentQuery): Promise<IncidentPage> {
    const filtered = mockIncidents.filter((incident) => {
      if (shiftId && incident.shiftId !== shiftId) return false;
      if (status && incident.status !== status) return false;
      if (service && incident.service !== service) return false;
      if (environment && incident.environment !== environment) return false;
      if (typeof hasLog === "boolean" && incident.hasLog !== hasLog) return false;
      return true;
    });
    const items = limit ? filtered.slice(offset, offset + limit) : filtered.slice(offset);
    return { items, total: filtered.length };
  }

  async get(id: string) {
    return mockIncidents.find((incident) => incident.id === id) ?? null;
  }

  async create(input: IncidentCreateInput): Promise<Incident> {
    const displayId = `INC-${1042 + mockIncidents.length + 1}`;
    const incident: Incident = {
      id: displayId,
      displayId,
      title: input.title,
      service: input.service,
      environment: input.environment,
      status: "open",
      triggeredAt: input.triggeredAt,
      hasLog: false,
      analysisStatus: "not_analyzed",
    };
    mockIncidents.unshift(incident);
    return incident;
  }

  async update(id: string, input: IncidentUpdateInput): Promise<Incident> {
    const incident = mockIncidents.find((i) => i.id === id);
    if (!incident) throw new Error("Incident not found");
    if (input.title !== undefined) incident.title = input.title;
    if (input.status !== undefined) incident.status = input.status;
    return incident;
  }

  async delete(id: string): Promise<void> {
    const index = mockIncidents.findIndex((i) => i.id === id);
    if (index !== -1) mockIncidents.splice(index, 1);
  }

  async getDashboardSummary() {
    return {
      openIncidents: mockIncidents.filter((i) => i.status !== "recovered").length,
      activeAlerts: 4,
      recoveredAlerts: mockIncidents.filter((i) => i.status === "recovered").length,
      logsAwaitingAnalysis: mockIncidents.filter((i) => i.analysisStatus === "not_analyzed" && i.hasLog).length,
      analysesRunning: mockIncidents.filter((i) => i.analysisStatus === "running").length,
      reportsGeneratedThisShift: 1,
    };
  }

  async getTimeline(id: string): Promise<IncidentTimelineEvent[]> {
    const incident = mockIncidents.find((i) => i.id === id);
    if (!incident) return [];
    const events: IncidentTimelineEvent[] = [
      { eventType: "triggered", label: "Triggered", occurredAt: incident.triggeredAt },
      { eventType: "incident_created", label: "Incident created", occurredAt: incident.triggeredAt },
    ];
    if (incident.hasLog) {
      events.push({ eventType: "evidence_attached", label: "Log attached", occurredAt: incident.triggeredAt });
    }
    if (incident.analysisStatus !== "not_analyzed") {
      events.push({ eventType: "job_requested", label: "Analysis requested", occurredAt: incident.triggeredAt });
    }
    if (incident.analysisStatus === "completed") {
      events.push({ eventType: "job_completed", label: "Analysis completed", occurredAt: incident.triggeredAt });
    }
    if (incident.status === "recovered") {
      events.push({ eventType: "recovered", label: "Recovered", occurredAt: incident.triggeredAt });
    }
    return events;
  }
}
