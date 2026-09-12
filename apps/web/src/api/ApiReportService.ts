import type { ReportGenerateInput, ReportService } from "@/services/report.service";
import type { ReportRun } from "@/types/report";
import { httpRequest, httpRequestBlob } from "@/lib/http";

interface RawReportOut {
  id: string;
  shift_id: string;
  snapshot_id: string;
  job_id: string;
  version: number;
  status: string;
  model: string | null;
  effort: string | null;
  skill_name: string | null;
  skill_version: string | null;
  generated_by: string | null;
  generated_at: string | null;
  error_message: string | null;
  created_at: string;
  downloadable: boolean;
}

function toRun(raw: RawReportOut): ReportRun {
  return {
    id: raw.id,
    shiftId: raw.shift_id,
    snapshotId: raw.snapshot_id,
    jobId: raw.job_id,
    version: raw.version,
    status: raw.status as ReportRun["status"],
    model: raw.model,
    effort: raw.effort,
    skillName: raw.skill_name,
    skillVersion: raw.skill_version,
    generatedBy: raw.generated_by,
    generatedAt: raw.generated_at,
    errorMessage: raw.error_message,
    createdAt: raw.created_at,
    downloadable: raw.downloadable,
  };
}

export class ApiReportService implements ReportService {
  async generate(shiftId: string, input: ReportGenerateInput): Promise<ReportRun> {
    const raw = await httpRequest<RawReportOut>(`/api/v1/shifts/${shiftId}/reports`, {
      method: "POST",
      body: { model: input.model, effort: input.effort },
    });
    return toRun(raw);
  }

  async list(shiftId: string): Promise<ReportRun[]> {
    const raws = await httpRequest<RawReportOut[]>(`/api/v1/shifts/${shiftId}/reports`);
    return raws.map(toRun);
  }

  async getDownloadUrl(reportId: string): Promise<string> {
    const raw = await httpRequest<{ download_url: string }>(`/api/v1/reports/${reportId}/download-url`);
    return raw.download_url;
  }

  async download(reportId: string): Promise<Blob> {
    return httpRequestBlob(`/api/v1/reports/${reportId}/download`);
  }
}
