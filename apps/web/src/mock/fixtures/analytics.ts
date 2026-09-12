import type { BarDatum } from "@/components/analytics/BarChart";
import type { DonutSlice } from "@/components/analytics/DonutChart";

/**
 * Mock analytics aggregates — Milestone 7, V1 scope only (master plan
 * Analytics table: incidents/day, incidents/shift, alerts by service, top
 * recurring alert titles, recovered vs unresolved, analysis job
 * success/failure, report generation counts). V2 metrics (MTBA, semantic
 * clusters, heatmaps, operator workload) are explicitly out of scope until
 * PostgreSQL aggregates replace this in Milestone 16.
 */
export const incidentsByDay: BarDatum[] = [
  { label: "Mon", value: 12 },
  { label: "Tue", value: 9 },
  { label: "Wed", value: 14 },
  { label: "Thu", value: 7 },
  { label: "Fri", value: 16 },
  { label: "Sat", value: 5 },
  { label: "Sun", value: 4 },
];

export const incidentsByShift: BarDatum[] = [
  { label: "Day", value: 22 },
  { label: "Swing", value: 18 },
  { label: "Night", value: 27 },
];

export const alertsByService: BarDatum[] = [
  { label: "payments-api", value: 19 },
  { label: "auth-service", value: 14 },
  { label: "order-svc", value: 11 },
  { label: "inventory-db", value: 8 },
  { label: "notify-worker", value: 6 },
];

export const recoveredVsUnresolved: DonutSlice[] = [
  { label: "Recovered", value: 58, color: "var(--color-good)" },
  { label: "Investigating", value: 11, color: "var(--color-warning)" },
  { label: "Open", value: 5, color: "var(--color-critical)" },
];

export const analysisJobOutcomes: DonutSlice[] = [
  { label: "Completed", value: 142, color: "var(--color-good)" },
  { label: "Failed", value: 6, color: "var(--color-critical)" },
];

export const topRecurringAlertTitles: { title: string; count: number }[] = [
  { title: "Payment gateway timeout", count: 14 },
  { title: "Database connection pool exhausted", count: 11 },
  { title: "Auth token validation failure", count: 9 },
  { title: "Queue consumer lag threshold exceeded", count: 7 },
  { title: "Disk usage above 85%", count: 6 },
];

export const reportGenerationCounts = { thisShift: 3, today: 9, thisWeek: 41 };
