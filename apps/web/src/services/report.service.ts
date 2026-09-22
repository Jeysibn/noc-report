import type { ReportDocument, ReportRun } from "@/types/report";

export type ReportGenerateInput = Record<string, never>

/** Report generation and immutable artifact retrieval contract. */
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
  /**
   * The same composed ReportDocument the DOCX was rendered from (see
   * the report composition module), as browser-safe JSON —
   * Alerts -> General Summary -> Log Analysis, screenshots referenced by
   * index only. Not every report has one (older frozen snapshots may
   * have used the legacy renderer profile), so callers should treat a
   * 404 as "no preview available" rather than an error.
   */
  getDocument(reportId: string): Promise<ReportDocument>;
  /** Fetches one preview screenshot by its index in the document, proxied
   * through the API (never a direct MinIO bucket/key) as a Blob for an
   * object URL. */
  getScreenshotBlob(reportId: string, index: number): Promise<Blob>;
}
