import type { SearchFilters, SearchResult } from "@/types/search";

export interface SearchResponse {
  items: SearchResult[];
  total: number;
}

/** Search contract backed by the PostgreSQL search endpoint in production. */
export interface SearchService {
  search(filters: SearchFilters): Promise<SearchResponse>;
}
