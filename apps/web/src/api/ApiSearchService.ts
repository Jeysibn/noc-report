import type { SearchService, SearchResponse } from "@/services/search.service";
import type { SearchFilters, SearchResult } from "@/types/search";
import { httpRequest } from "@/lib/http";

interface RawSearchResultOut {
  id: string;
  display_id: string;
  title: string;
  service: string;
  environment: string;
  status: string;
  triggered_at: string;
  has_log: boolean;
  analysis_summary: string | null;
  recurrence_count: number;
}

interface RawSearchResponse {
  items: RawSearchResultOut[];
  total: number;
}

function toResult(raw: RawSearchResultOut): SearchResult {
  return {
    id: raw.id,
    displayId: raw.display_id,
    title: raw.title,
    service: raw.service,
    environment: raw.environment,
    status: raw.status,
    triggeredAt: raw.triggered_at,
    hasLog: raw.has_log,
    analysisSummary: raw.analysis_summary,
    recurrenceCount: raw.recurrence_count,
  };
}

export class ApiSearchService implements SearchService {
  async search(filters: SearchFilters): Promise<SearchResponse> {
    const raw = await httpRequest<RawSearchResponse>("/api/v1/search", {
      query: {
        q: filters.q || undefined,
        service: filters.service,
        environment: filters.environment,
        status: filters.status,
        has_log: filters.hasLog,
        date_from: filters.dateFrom,
        date_to: filters.dateTo,
        limit: 200,
      },
    });
    return { items: raw.items.map(toResult), total: raw.total };
  }
}
