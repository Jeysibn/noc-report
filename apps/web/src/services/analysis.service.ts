import type { AnalysisRun } from "@/types/analysis";

export type AnalysisRequestInput = Record<string, never>

/**
 * Real-only from the start (Milestone 13) — the old mock's jobSimulator
 * lived directly inside LogAnalysisPanel with no service interface behind
 * it; this is the first one. No fallback analyzer here either: a request
 * that cannot run while the runtime is unconfigured fails explicitly.
 */
export interface AnalysisService {
  requestAnalysis(incidentId: string, input: AnalysisRequestInput): Promise<AnalysisRun>;
  listRuns(incidentId: string): Promise<AnalysisRun[]>;
  getRun(incidentId: string, jobId: string): Promise<AnalysisRun>;
}
