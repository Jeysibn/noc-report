import type { AnalysisRequestInput, AnalysisService } from "@/services/analysis.service";
import type { AnalysisFind, AnalysisRun, SeveritySignal } from "@/types/analysis";
import { httpRequest } from "@/lib/http";

interface RawFind {
  id?: string;
  label_en: string;
  label_zh: string;
  count: number | null;
  percentage: number | null;
  pattern_ids?: string[];
  detail_en: string;
  detail_zh: string;
}

interface RawAnalysisRunOut {
  job_id: string;
  incident_id: string | null;
  status: string;
  model: string | null;
  effort: string | null;
  skill_name: string | null;
  skill_version: string | null;
  queued_at: string;
  completed_at: string | null;
  error_message: string | null;
  run_id: string | null;
  result: {
    total_entries?: number;
    summary_en: string;
    summary_zh: string;
    key_finds: RawFind[];
    secondary_finds: RawFind[];
    severity_signal: string;
    confidence: number;
  } | null;
  current: boolean;
}

function toFind(raw: RawFind): AnalysisFind {
  return {
    id: raw.id,
    labelEn: raw.label_en,
    labelZh: raw.label_zh,
    count: raw.count,
    percentage: raw.percentage,
    patternIds: raw.pattern_ids,
    detailEn: raw.detail_en,
    detailZh: raw.detail_zh,
  };
}

function toRun(raw: RawAnalysisRunOut): AnalysisRun {
  return {
    jobId: raw.job_id,
    status: raw.status as AnalysisRun["status"],
    model: raw.model,
    effort: raw.effort,
    skillVersion: raw.skill_version,
    queuedAt: raw.queued_at,
    completedAt: raw.completed_at,
    errorMessage: raw.error_message,
    isCurrent: raw.current,
    result: raw.result
      ? {
          totalEntries: raw.result.total_entries,
          summaryEn: raw.result.summary_en,
          summaryZh: raw.result.summary_zh,
          keyFinds: (raw.result.key_finds ?? []).map(toFind),
          secondaryFinds: (raw.result.secondary_finds ?? []).map(toFind),
          severitySignal: raw.result.severity_signal as SeveritySignal,
          confidence: raw.result.confidence,
        }
      : undefined,
  };
}

export class ApiAnalysisService implements AnalysisService {
  async requestAnalysis(incidentId: string, input: AnalysisRequestInput): Promise<AnalysisRun> {
    const raw = await httpRequest<RawAnalysisRunOut>(`/api/v1/incidents/${incidentId}/analysis-runs`, {
      method: "POST",
      body: input,
    });
    return toRun(raw);
  }

  async listRuns(incidentId: string): Promise<AnalysisRun[]> {
    const raws = await httpRequest<RawAnalysisRunOut[]>(`/api/v1/incidents/${incidentId}/analysis-runs`);
    return raws.map(toRun);
  }

  async getRun(incidentId: string, jobId: string): Promise<AnalysisRun> {
    const raw = await httpRequest<RawAnalysisRunOut>(`/api/v1/incidents/${incidentId}/analysis-runs/${jobId}`);
    return toRun(raw);
  }
}
