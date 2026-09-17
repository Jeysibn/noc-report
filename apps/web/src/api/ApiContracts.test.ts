import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, httpRequest } from "@/lib/http";
import { ApiAnalysisService } from "./ApiAnalysisService";
import { ApiIncidentService } from "./ApiIncidentService";

vi.mock("@/lib/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/http")>();
  return { ...actual, httpRequest: vi.fn() };
});

const request = vi.mocked(httpRequest);

describe("API integration contracts", () => {
  beforeEach(() => request.mockReset());

  it("uses the authoritative dashboard aggregate and forwards has-log filtering", async () => {
    request.mockResolvedValueOnce({
      open_incidents: 201,
      active_alerts: 201,
      recovered_alerts: 0,
      logs_awaiting_analysis: 3,
      analyses_running: 1,
      reports_generated_this_shift: 2,
    });
    const incidents = new ApiIncidentService();
    expect(await incidents.getDashboardSummary()).toEqual({
      openIncidents: 201,
      activeAlerts: 201,
      recoveredAlerts: 0,
      logsAwaitingAnalysis: 3,
      analysesRunning: 1,
      reportsGeneratedThisShift: 2,
    });
    expect(request).toHaveBeenCalledWith("/api/v1/dashboard/summary");

    request.mockResolvedValueOnce({ items: [], total: 0 });
    await incidents.list({ limit: 20, offset: 20, hasLog: true });
    expect(request).toHaveBeenLastCalledWith("/api/v1/incidents", {
      query: expect.objectContaining({ has_log: true }),
    });
  });

  it("only maps 404 to not-found and preserves operational failures", async () => {
    const incidents = new ApiIncidentService();
    request.mockRejectedValueOnce(new ApiError(404, "missing"));
    await expect(incidents.get("missing")).resolves.toBeNull();

    request.mockRejectedValueOnce(new ApiError(500, "database unavailable"));
    await expect(incidents.get("incident-1")).rejects.toMatchObject({ status: 500 });

    request.mockRejectedValueOnce(new Error("network down"));
    await expect(incidents.get("incident-1")).rejects.toThrow("network down");
  });

  it("omits analysis policy overrides for System default and sends explicit overrides", async () => {
    request.mockResolvedValue({
      job_id: "job-1", incident_id: "incident-1", status: "QUEUED", model: null,
      effort: null, skill_name: null, skill_version: null, queued_at: "now",
      completed_at: null, error_message: null, run_id: null, result: null, current: true,
    });
    const analysis = new ApiAnalysisService();
    await analysis.requestAnalysis("incident-1", {});
    expect(request).toHaveBeenLastCalledWith("/api/v1/incidents/incident-1/analysis-runs", {
      method: "POST", body: {},
    });
    await analysis.requestAnalysis("incident-1", { model: "claude-opus-5", effort: "high" });
    expect(request).toHaveBeenLastCalledWith("/api/v1/incidents/incident-1/analysis-runs", {
      method: "POST", body: { model: "claude-opus-5", effort: "high" },
    });
  });
});
