import type { SearchFilters, SearchResult } from "@/types/search";

export interface SearchResponse {
  items: SearchResult[];
  total: number;
}

/**
 * Real-only from the start (Milestone 15) — replaces the old client-side
 * substring filter KnowledgeBase.tsx used to run over mock incident
 * fixtures with the real PostgreSQL FTS/trigram search endpoint.
 */
export interface SearchService {
  search(filters: SearchFilters): Promise<SearchResponse>;
}
