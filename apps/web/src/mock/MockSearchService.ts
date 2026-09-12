import type { SearchService, SearchResponse } from "@/services/search.service";
import type { SearchFilters, SearchResult } from "@/types/search";
import { mockIncidents } from "./fixtures/incidents";

/**
 * Client-only test double — real search (Milestone 15) is PostgreSQL
 * FTS/trigram, see ApiSearchService. This mirrors the shape closely
 * enough for component tests that don't hit a backend.
 */
export class MockSearchService implements SearchService {
  async search(filters: SearchFilters): Promise<SearchResponse> {
    const q = filters.q?.trim().toLowerCase();
    const items: SearchResult[] = mockIncidents
      .filter((incident) => {
        if (filters.service && incident.service !== filters.service) return false;
        if (filters.environment && incident.environment !== filters.environment) return false;
        if (typeof filters.hasLog === "boolean" && incident.hasLog !== filters.hasLog) return false;
        if (!q) return true;
        return (
          incident.title.toLowerCase().includes(q) ||
          incident.service.toLowerCase().includes(q) ||
          incident.environment.toLowerCase().includes(q)
        );
      })
      .map((incident) => ({
        id: incident.id,
        displayId: incident.displayId,
        title: incident.title,
        service: incident.service,
        environment: incident.environment,
        status: incident.status,
        triggeredAt: incident.triggeredAt,
        hasLog: incident.hasLog,
        analysisSummary: null,
        recurrenceCount: 0,
      }));
    return { items, total: items.length };
  }
}
