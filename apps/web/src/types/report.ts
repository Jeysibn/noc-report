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
  previewable: boolean;
  reportVersionId: string | null;
}

// Mirrors bridge/noc_bridge/report_document_json.py's browser-safe
// serialization of the same ReportDocument the DOCX adapter renders from
// (bridge/noc_bridge/report_document.py) — one semantic model, two
// adapters. Every block carries a "type" discriminator matching the
// bridge's own block-type names.

export interface ReportMetadataItem {
  type: "metadata";
  label: string;
  value: string;
}

export interface ReportLink {
  type: "link";
  label: string;
  prefix: string | null;
  url: string;
  text: string | null;
}

export interface ReportLogFileReference {
  type: "log_file_reference";
  filename: string;
  url: string | null;
}

export interface ReportScreenshotRef {
  type: "screenshot";
  index: number;
  filename: string | null;
}

export interface ReportAlertNavigation {
  type: "alert_navigation";
  entries: Array<{ text: string; target: string }>;
}

export interface ReportHeading {
  type: "heading";
  text: string;
  level: number;
}

export interface ReportParagraph {
  type: "paragraph";
  text: string;
  style: string | null;
}

export interface ReportDivider {
  type: "divider";
}

export interface ReportPageBreak {
  type: "page_break";
}

export interface ReportBilingualText {
  type: "bilingual_text";
  text_zh: string;
  text_en: string;
  heading_zh: string | null;
  heading_en: string | null;
}

export interface ReportFind {
  label: string;
  detail: string | null;
  stat: string | null;
}

export interface ReportBilingualFindList {
  type: "bilingual_find_list";
  heading_zh: string;
  heading_en: string;
  finds_zh: ReportFind[];
  finds_en: ReportFind[];
}

export interface ReportFindList {
  type: "find_list";
  heading: string;
  language: string | null;
  finds: ReportFind[];
}

export interface ReportIncidentEvidence {
  type: "incident_evidence";
  heading: string;
  incident_id?: string | null;
  metadata: ReportMetadataItem[];
  links: ReportLink[];
  log_file: ReportLogFileReference | null;
  screenshots: ReportScreenshotRef[];
  navigation_target?: string | null;
}

export interface ReportAnalysisReference {
  type: "analysis_reference";
  heading: string;
  available: boolean;
  incident_id?: string | null;
  analysis_run_id?: string | null;
  unavailable_text: string | null;
  metadata?: ReportMetadataItem[];
  children: ReportBlock[];
  screenshots: ReportScreenshotRef[];
  log_file: ReportLogFileReference | null;
  summary: ReportBilingualText | null;
  key_finds: ReportBilingualFindList | null;
  secondary_finds: ReportBilingualFindList | null;
  likely_cause: ReportBilingualText | null;
  recommended_action: ReportBilingualText | null;
  provenance?: ReportMetadataItem[];
  bookmark?: string | null;
}

export type ReportBlock =
  | ReportHeading
  | ReportParagraph
  | ReportDivider
  | ReportPageBreak
  | ReportMetadataItem
  | ReportLink
  | ReportLogFileReference
  | ReportScreenshotRef
  | ReportAlertNavigation
  | ReportBilingualText
  | ReportBilingualFindList
  | ReportFindList
  | ReportIncidentEvidence
  | ReportAnalysisReference;

export interface ReportDocument {
  title: string;
  metadata: ReportMetadataItem[];
  blocks: ReportBlock[];
  /** Present only for an explicitly authorized audit payload; normal preview omits it. */
  provenance?: ReportMetadataItem[];
}
