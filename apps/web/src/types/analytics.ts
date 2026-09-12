export interface AnalyticsBarDatum {
  label: string;
  value: number;
}

export interface AnalyticsDonutSlice {
  label: string;
  value: number;
}

export interface AnalyticsTopAlertTitle {
  title: string;
  count: number;
}

export interface AnalyticsReportGenerationCounts {
  thisShift: number;
  today: number;
  thisWeek: number;
}

export interface AnalyticsSummary {
  incidentsByDay: AnalyticsBarDatum[];
  incidentsByShift: AnalyticsBarDatum[];
  alertsByService: AnalyticsBarDatum[];
  recoveredVsUnresolved: AnalyticsDonutSlice[];
  analysisJobOutcomes: AnalyticsDonutSlice[];
  topRecurringAlertTitles: AnalyticsTopAlertTitle[];
  reportGenerationCounts: AnalyticsReportGenerationCounts;
}
