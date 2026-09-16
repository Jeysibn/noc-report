export type DependencyStatus = "healthy" | "degraded" | "unavailable" | "unknown" | "disabled" | "not_applicable";

export interface DependencyHealth {
  status: DependencyStatus;
  detail?: unknown;
}

export interface OperationalHealth {
  status: DependencyStatus;
  dependency_status?: DependencyStatus;
  pipeline?: {
    status: "healthy" | "degraded";
    detail?: unknown;
  };
  checked_at: string;
  dependencies: Record<string, DependencyHealth>;
}
