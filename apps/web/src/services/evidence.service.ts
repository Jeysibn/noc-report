/** Evidence types match app/models/models.py's Evidence.evidence_type column. */
export type EvidenceType = "ALERT_SCREENSHOT" | "LOG" | "GRAFANA_SCREENSHOT" | "SUPPORTING_DOCUMENT" | "OTHER";

export interface EvidenceRecord {
  id: string;
  evidenceType: EvidenceType;
  originalFilename: string;
  mimeType?: string;
  byteSize?: number;
  createdAt?: string;
  lifecycleState: "ACTIVE" | "PURGE_PENDING" | "PURGED" | string;
}

/** The browser-facing evidence contract; storage coordinates stay server-side. */
export interface EvidenceService {
  upload(incidentId: string, file: File, evidenceType: EvidenceType): Promise<EvidenceRecord>;
  list(incidentId: string): Promise<EvidenceRecord[]>;
  getDownloadUrl(evidenceId: string): Promise<string>;
  delete(evidenceId: string): Promise<void>;
}
