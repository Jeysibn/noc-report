import type { EvidenceRecord, EvidenceService, EvidenceType } from "@/services/evidence.service";
import { httpRequest, putFile } from "@/lib/http";

interface RawEvidence {
  id: string;
  evidence_type: string;
  original_filename: string;
}

/**
 * Real evidence upload is a three-request choreography, not a single
 * multipart POST (see apps/api/app/api/v1/routers/evidence.py):
 *   1. ask the API for a presigned upload URL
 *   2. PUT the file bytes straight to MinIO at that URL
 *   3. tell the API the opaque upload intent completed, so it can create the
 *      Evidence row from server-owned storage identity
 */
export class ApiEvidenceService implements EvidenceService {
  async upload(incidentId: string, file: File, evidenceType: EvidenceType): Promise<EvidenceRecord> {
    const uploadReq = await httpRequest<{ upload_id: string; upload_url: string; expires_at: string }>(
      `/api/v1/incidents/${incidentId}/evidence/upload-url`,
      {
        method: "POST",
        body: {
          evidence_type: evidenceType,
          filename: file.name,
          content_type: file.type || undefined,
          expected_byte_size: file.size,
        },
      },
    );

    await putFile(uploadReq.upload_url, file);

    const complete = await httpRequest<RawEvidence>(`/api/v1/incidents/${incidentId}/evidence/complete`, {
      method: "POST",
      body: {
        upload_id: uploadReq.upload_id,
      },
    });

    return { id: complete.id, evidenceType: complete.evidence_type as EvidenceType, originalFilename: complete.original_filename };
  }

  async list(incidentId: string): Promise<EvidenceRecord[]> {
    const raws = await httpRequest<RawEvidence[]>(`/api/v1/incidents/${incidentId}/evidence`);
    return raws.map((r) => ({ id: r.id, evidenceType: r.evidence_type as EvidenceType, originalFilename: r.original_filename }));
  }
}
