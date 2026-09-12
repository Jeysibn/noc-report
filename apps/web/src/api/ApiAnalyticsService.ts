import type { AnalyticsService } from "@/services/analytics.service";
import type { AnalyticsSummary } from "@/types/analytics";
import { httpRequest } from "@/lib/http";

interface RawBarDatum {
  label: string;
  value: number;
}

interface RawDonutSlice {
  label: string;
  value: number;
}

interface RawTopAlertTitle {
  title: string;
  count: number;
}

interface RawReportGenerationCounts {
  this_shift: number;
  today: number;
  this_week: number;
}

interface RawAnalyticsSummary {
  incidents_by_day: RawBarDatum[];
  incidents_by_shift: RawBarDatum[];
  alerts_by_service: RawBarDatum[];
  recovered_vs_unresolved: RawDonutSlice[];
  analysis_job_outcomes: RawDonutSlice[];
  top_recurring_alert_titles: RawTopAlertTitle[];
  report_generation_counts: RawReportGenerationCounts;
}

function toSummary(raw: RawAnalyticsSummary): AnalyticsSummary {
  return {
    incidentsByDay: raw.incidents_by_day,
    incidentsByShift: raw.incidents_by_shift,
    alertsByService: raw.alerts_by_service,
    recoveredVsUnresolved: raw.recovered_vs_unresolved,
    analysisJobOutcomes: raw.analysis_job_outcomes,
    topRecurringAlertTitles: raw.top_recurring_alert_titles.map((row) => ({
      title: row.title,
      count: row.count,
    })),
    reportGenerationCounts: {
      thisShift: raw.report_generation_counts.this_shift,
      today: raw.report_generation_counts.today,
      thisWeek: raw.report_generation_counts.this_week,
    },
  };
}

export class ApiAnalyticsService implements AnalyticsService {
  async getSummary(): Promise<AnalyticsSummary> {
    const raw = await httpRequest<RawAnalyticsSummary>("/api/v1/analytics/summary");
    return toSummary(raw);
  }
}
