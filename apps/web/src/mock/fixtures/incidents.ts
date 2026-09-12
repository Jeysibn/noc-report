import type { Incident } from "@/types/domain";

const services = ["payments-api", "checkout-web", "auth-service", "infra", "search-api", "notifications"];
const environments = ["production", "staging"] as const;
const statuses: Incident["status"][] = ["open", "investigating", "recovered"];
const analysisStatuses: Incident["analysisStatus"][] = [
  "not_analyzed",
  "queued",
  "running",
  "completed",
  "failed",
];

const titles = [
  "Payment gateway timeout",
  "Elevated 5xx on checkout",
  "Disk usage alert, node-04",
  "Auth token refresh failures",
  "Search index lag",
  "Notification delivery delay",
  "Database connection pool exhausted",
  "CDN cache miss spike",
  "Memory leak in worker process",
  "Rate limiter false positives",
];

function seededRandom(seed: number) {
  let value = seed;
  return () => {
    value = (value * 1103515245 + 12345) & 0x7fffffff;
    return value / 0x7fffffff;
  };
}

function generateIncidents(count: number): Incident[] {
  const rand = seededRandom(42);
  const items: Incident[] = [];
  const baseTime = new Date("2026-09-09T02:00:00Z").getTime();

  for (let i = 0; i < count; i++) {
    const status = statuses[Math.floor(rand() * statuses.length)];
    const hasLog = rand() > 0.25;
    const analysisStatus = hasLog
      ? analysisStatuses[Math.floor(rand() * analysisStatuses.length)]
      : "not_analyzed";
    const displayId = `INC-${1042 - i}`;
    items.push({
      id: displayId,
      displayId,
      title: titles[i % titles.length],
      service: services[Math.floor(rand() * services.length)],
      environment: environments[Math.floor(rand() * environments.length)],
      status,
      triggeredAt: new Date(baseTime - i * 37 * 60 * 1000).toISOString(),
      hasLog,
      analysisStatus,
    });
  }
  return items;
}

/** 120 mock incidents — enough to prove the list/filter UX stays smooth at 100+, per the UI Phase Plan. */
export const mockIncidents: Incident[] = generateIncidents(120);
