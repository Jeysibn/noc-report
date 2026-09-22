/**
 * Shapes mirror apps/api's real AnalysisRunOut/log-triage-summary skill
 * contract. Bilingual (Chinese/English) with a Key Finds/Secondary Finds
 * breakdown used by the log-triage-summary skill.
 */
/**
 * Mirrors the bridge's actual job.status values (bridge/noc_bridge/db.py:
 * mark_started/mark_completed/mark_failed) plus the client-only
 * "NOT_ANALYZED" placeholder for an incident with no run yet — the backend
 * only ever writes QUEUED/PROCESSING/COMPLETED/FAILED, it has no STARTING
 * or RUNNING state, so those never matched and left statusPill lookups
 * undefined for any in-flight job.
 */
export type AnalysisJobStatus = "NOT_ANALYZED" | "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";

export type SeveritySignal = "low" | "medium" | "high" | "critical";

export interface AnalysisFind {
  labelEn: string;
  labelZh: string;
  count: number | null;
  percentage: number | null;
  patternIds?: string[];
  detailEn: string;
  detailZh: string;
}

export interface AnalysisResult {
  /** Exact physical log-entry total supplied by the deterministic runtime. */
  totalEntries?: number;
  summaryEn: string;
  summaryZh: string;
  keyFinds: AnalysisFind[];
  secondaryFinds: AnalysisFind[];
  severitySignal: SeveritySignal;
  confidence: number;
}

export interface AnalysisRun {
  jobId: string;
  status: AnalysisJobStatus;
  model: string | null;
  effort: string | null;
  skillVersion: string | null;
  queuedAt: string;
  completedAt: string | null;
  errorMessage: string | null;
  result?: AnalysisResult;
  isCurrent: boolean;
}
