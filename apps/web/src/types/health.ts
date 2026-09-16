export type DependencyStatus = "healthy" | "degraded" | "unavailable" | "unknown";

export interface DependencyHealth {
  status: DependencyStatus;
  detail?: unknown;
}

export interface OperationalHealth {
  status: DependencyStatus;
  checked_at: string;
  dependencies: Record<string, DependencyHealth>;
}
