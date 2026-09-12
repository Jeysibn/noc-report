import { useEffect, useState } from "react";
import { Card, CardTitle } from "@/components/ui/Card";
import { RequireRole } from "@/components/layout/RequireRole";
import { BarChart } from "@/components/analytics/BarChart";
import { DonutChart } from "@/components/analytics/DonutChart";
import { analyticsService } from "@/services";
import type { AnalyticsSummary } from "@/types/analytics";

const RECOVERED_COLORS: Record<string, string> = {
  Recovered: "var(--color-good)",
  Investigating: "var(--color-warning)",
  Open: "var(--color-critical)",
};

const OUTCOME_COLORS: Record<string, string> = {
  Completed: "var(--color-good)",
  Failed: "var(--color-critical)",
};

/**
 * Analytics (Milestone 16), V1 scope only — DevOps/Admin. Real PostgreSQL
 * aggregates (GET /api/v1/analytics/summary) replace the Milestone 7 mock
 * fixtures; charts stay the same hand-rolled SVG components from
 * `src/components/analytics/*` — a real charting library still isn't
 * justified by what these six small series need.
 */
export function Analytics() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    analyticsService
      .getSummary()
      .then(setSummary)
      .finally(() => setLoading(false));
  }, []);

  return (
    <RequireRole roles={["DevOps", "Admin"]}>
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-xl font-semibold">Analytics</h1>
          <p className="text-sm text-muted">DevOps/Admin visibility. V1 metrics only.</p>
        </div>

        {loading || !summary ? (
          <Card>
            <p className="text-sm text-muted">Loading analytics...</p>
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <Card>
                <CardTitle>Incidents per day</CardTitle>
                <div className="mt-4">
                  <BarChart data={summary.incidentsByDay} />
                </div>
              </Card>
              <Card>
                <CardTitle>Incidents per shift</CardTitle>
                <div className="mt-4">
                  <BarChart data={summary.incidentsByShift} color="var(--color-info)" />
                </div>
              </Card>
              <Card>
                <CardTitle>Alerts by service</CardTitle>
                <div className="mt-4">
                  <BarChart data={summary.alertsByService} color="var(--color-warning)" />
                </div>
              </Card>
              <Card>
                <CardTitle>Recovered vs unresolved</CardTitle>
                <div className="mt-4">
                  <DonutChart
                    data={summary.recoveredVsUnresolved.map((slice) => ({
                      ...slice,
                      color: RECOVERED_COLORS[slice.label] ?? "var(--color-muted)",
                    }))}
                  />
                </div>
              </Card>
              <Card>
                <CardTitle>Analysis job outcomes</CardTitle>
                <div className="mt-4">
                  <DonutChart
                    data={summary.analysisJobOutcomes.map((slice) => ({
                      ...slice,
                      color: OUTCOME_COLORS[slice.label] ?? "var(--color-muted)",
                    }))}
                  />
                </div>
              </Card>
              <Card>
                <CardTitle>Report generations</CardTitle>
                <div className="mt-4 grid grid-cols-3 gap-4 text-center">
                  <div>
                    <p className="font-data text-2xl font-semibold">
                      {summary.reportGenerationCounts.thisShift}
                    </p>
                    <p className="text-xs text-muted">This shift</p>
                  </div>
                  <div>
                    <p className="font-data text-2xl font-semibold">{summary.reportGenerationCounts.today}</p>
                    <p className="text-xs text-muted">Today</p>
                  </div>
                  <div>
                    <p className="font-data text-2xl font-semibold">
                      {summary.reportGenerationCounts.thisWeek}
                    </p>
                    <p className="text-xs text-muted">This week</p>
                  </div>
                </div>
              </Card>
            </div>

            <Card>
              <CardTitle>Top recurring alert titles</CardTitle>
              {summary.topRecurringAlertTitles.length === 0 ? (
                <p className="mt-4 text-sm text-muted">No alert title has recurred yet.</p>
              ) : (
                <ul className="mt-4 flex flex-col gap-2">
                  {summary.topRecurringAlertTitles.map((row) => (
                    <li
                      key={row.title}
                      className="flex items-center justify-between border-t border-border pt-2 text-sm first:border-t-0 first:pt-0"
                    >
                      <span>{row.title}</span>
                      <span className="font-data font-medium text-muted">×{row.count}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </>
        )}
      </div>
    </RequireRole>
  );
}
