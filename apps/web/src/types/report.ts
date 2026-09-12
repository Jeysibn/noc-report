// Mirrors the bridge's real job.status values (bridge/noc_bridge/db.py) —
// there is no STARTING/RUNNING state, only QUEUED/PROCESSING/COMPLETED/FAILED.
export type ReportJobStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";

export interface ReportRun {
  id: string;
  shiftId: string;
  snapshotId: string;
  jobId: string;
  version: number;
  status: ReportJobStatus;
  model: string | null;
  effort: string | null;
  skillName: string | null;
  skillVersion: string | null;
  generatedBy: string | null;
  generatedAt: string | null;
  errorMessage: string | null;
  createdAt: string;
  downloadable: boolean;
}
