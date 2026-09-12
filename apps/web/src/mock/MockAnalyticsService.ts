import type { AnalyticsService } from "@/services/analytics.service";
import type { AnalyticsSummary } from "@/types/analytics";
import {
  incidentsByDay,
  incidentsByShift,
  alertsByService,
  recoveredVsUnresolved,
  analysisJobOutcomes,
  topRecurringAlertTitles,
  reportGenerationCounts,
} from "./fixtures/analytics";

/**
 * Client-only test double — real analytics (Milestone 16) are PostgreSQL
 * aggregates, see ApiAnalyticsService. Reuses the same fixture data the
 * pre-Milestone-16 Analytics page rendered directly, just behind the
 * service interface now.
 */
export class MockAnalyticsService implements AnalyticsService {
  async getSummary(): Promise<AnalyticsSummary> {
    return {
      incidentsByDay,
      incidentsByShift,
      alertsByService,
      recoveredVsUnresolved: recoveredVsUnresolved.map(({ label, value }) => ({ label, value })),
      analysisJobOutcomes: analysisJobOutcomes.map(({ label, value }) => ({ label, value })),
      topRecurringAlertTitles,
      reportGenerationCounts,
    };
  }
}
