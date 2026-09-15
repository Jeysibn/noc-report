export interface PrefillField {
  value: string | null;
  source_text: string | null;
  quality: "HIGH" | "REVIEW" | "UNRESOLVED";
  method: "deterministic" | "deterministic_fuzzy" | "local_ai" | "unresolved";
  ocr_confidence: number | null;
}

export interface IncidentPrefill {
  title: PrefillField;
  service: PrefillField;
  notes: PrefillField;
  status: PrefillField;
  triggered_at: PrefillField;
  recovered_at: PrefillField;
  trigger_value: PrefillField;
  environment: PrefillField;
  mapper_status: "DETERMINISTIC" | "LOCAL_AI" | "FALLBACK";
  mapper_model: string | null;
  mapper_error: string | null;
}

export interface IncidentPrefillRun {
  id: string;
  status: string;
  source_filename: string;
  source_sha256: string;
  raw_ocr_text: string | null;
  normalized_ocr_text: string | null;
  ocr_engine: string;
  ocr_engine_version: string | null;
  ocr_duration_ms: number | null;
  prefill_json: IncidentPrefill | null;
  prefill_model: string | null;
  prefill_duration_ms: number | null;
  error_message: string | null;
  incident_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface OcrService {
  prefill(file: File): Promise<IncidentPrefillRun>;
  attachPrefill(prefillId: string, incidentId: string): Promise<void>;
}
