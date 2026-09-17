import type { AnalysisRun } from "@/types/analysis";

export interface AnalysisRequestInput {
  /** Omitted means inherit the administrator's System AI Configuration. */
  model?: string;
  effort?: "low" | "medium" | "high";
}

/**
 * Real-only from the start (Milestone 13) — the old mock's jobSimulator
 * lived directly inside LogAnalysisPanel with no service interface behind
 * it; this is the first one. No fallback analyzer here either: a request
 * that never gets picked up by the bridge just stays QUEUED, truthfully.
 */
export interface AnalysisService {
  requestAnalysis(incidentId: string, input: AnalysisRequestInput): Promise<AnalysisRun>;
  listRuns(incidentId: string): Promise<AnalysisRun[]>;
  getRun(incidentId: string, jobId: string): Promise<AnalysisRun>;
}
