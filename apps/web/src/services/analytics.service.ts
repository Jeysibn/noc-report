import type { AnalyticsSummary } from "@/types/analytics";

export interface AnalyticsService {
  getSummary(): Promise<AnalyticsSummary>;
}
