import type { ReportRun } from "@/types/report";

export interface ReportGenerateInput {
  model: string;
  effort: "low" | "medium" | "high";
}

/**
 * Real-only from the start (Milestone 14) — mirrors analysis.service.ts's
 * shape. generate() freezes whatever incident/analysis state currently
 * exists for the shift; it does not orchestrate missing analyses first
 * (that was the old mock's fiction — the real generate_report endpoint
 * just freezes a snapshot of what's there).
 */
export interface ReportService {
  generate(shiftId: string, input: ReportGenerateInput): Promise<ReportRun>;
  list(shiftId: string): Promise<ReportRun[]>;
  getDownloadUrl(reportId: string): Promise<string>;
  /**
   * Fetches the DOCX through the API (same origin the app already talks
   * to) and returns a Blob, rather than a presigned MinIO URL the
   * browser may not be able to reach directly (see
   * app/api/v1/routers/reports.py's download_report). Callers save it
   * with an <a download> click, not window.open.
   */
  download(reportId: string): Promise<Blob>;
}
