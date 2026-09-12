/** Evidence types match app/models/models.py's Evidence.evidence_type column. */
export type EvidenceType = "ALERT_SCREENSHOT" | "LOG" | "GRAFANA_SCREENSHOT" | "SUPPORTING_DOCUMENT" | "OTHER";

export interface EvidenceRecord {
  id: string;
  evidenceType: EvidenceType;
  originalFilename: string;
}

/**
 * Real-only from the start (Milestone 13 frontend wiring) — there was no
 * mock evidence flow to swap out; the presigned-upload choreography (see
 * ApiEvidenceService) only makes sense against a real object store.
 */
export interface EvidenceService {
  upload(incidentId: string, file: File, evidenceType: EvidenceType): Promise<EvidenceRecord>;
  list(incidentId: string): Promise<EvidenceRecord[]>;
}
