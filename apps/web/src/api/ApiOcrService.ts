import { httpRequest, uploadRequest } from "@/lib/http";
import type { IncidentPrefillRun, OcrService } from "@/services/ocr.service";

export class ApiOcrService implements OcrService {
  async prefill(file: File): Promise<IncidentPrefillRun> {
    return uploadRequest<IncidentPrefillRun>("/api/v1/ocr/prefill", file);
  }

  async attachPrefill(prefillId: string, incidentId: string): Promise<void> {
    await httpRequest(`/api/v1/ocr/prefills/${prefillId}/attach/${incidentId}`, { method: "POST", body: {} });
  }
}
