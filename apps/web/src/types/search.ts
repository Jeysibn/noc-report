export interface SearchResult {
  id: string;
  displayId: string;
  title: string;
  service: string;
  environment: string;
  status: string;
  triggeredAt: string;
  hasLog: boolean;
  analysisSummary: string | null;
  recurrenceCount: number;
}

export interface SearchFilters {
  q?: string;
  service?: string;
  environment?: string;
  status?: string;
  hasLog?: boolean;
  dateFrom?: string;
  dateTo?: string;
}
