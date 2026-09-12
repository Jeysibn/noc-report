import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { searchService } from "@/services";
import type { SearchResult } from "@/types/search";
import { Card } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Form";
import { Table, Thead, Tbody, Tr, Th, Td } from "@/components/ui/Table";
import { formatTime } from "@/lib/incidentStatus";

const DEBOUNCE_MS = 300;

/**
 * Knowledge Base (Milestone 15: Search / Knowledge). Real PostgreSQL
 * FTS/trigram search (GET /api/v1/search, master plan §32) replaces the
 * old client-side substring filter over mock fixtures. Search is
 * debounced since every keystroke now hits a real endpoint.
 */
export function KnowledgeBase() {
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [service, setService] = useState("all");
  const [environment, setEnvironment] = useState("all");
  const [loading, setLoading] = useState(false);
  const [allResults, setAllResults] = useState<SearchResult[]>([]);

  // Unfiltered fetch once, just to populate the filter dropdowns with
  // values that actually occur — avoids hardcoding a service/environment
  // list that would drift from real data.
  useEffect(() => {
    searchService.search({}).then((res) => setAllResults(res.items));
  }, []);

  const services = useMemo(() => Array.from(new Set(allResults.map((r) => r.service))).sort(), [allResults]);
  const environments = useMemo(
    () => Array.from(new Set(allResults.map((r) => r.environment))).sort(),
    [allResults],
  );

  useEffect(() => {
    setLoading(true);
    const handle = setTimeout(() => {
      searchService
        .search({
          q: query.trim() || undefined,
          service: service === "all" ? undefined : service,
          environment: environment === "all" ? undefined : environment,
        })
        .then((res) => {
          setResults(res.items);
          setTotal(res.total);
        })
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query, service, environment]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Knowledge Base</h1>
        <p className="text-sm text-muted">
          Search past incidents by title, service, environment, notes, OCR text, or analysis text.
        </p>
      </div>

      <Card className="flex flex-wrap items-end gap-4">
        <div className="min-w-[240px] flex-1">
          <label className="mb-1.5 block text-sm font-medium">Search</label>
          <Input
            placeholder="Title, notes, OCR text, analysis text..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">Service</label>
          <Select value={service} onChange={(e) => setService(e.target.value)}>
            <option value="all">All services</option>
            {services.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">Environment</label>
          <Select value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            <option value="all">All environments</option>
            {environments.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </Select>
        </div>
      </Card>

      <Card>
        <p className="mb-4 text-sm text-muted">{loading ? "Searching..." : `${total} results`}</p>
        <Table>
          <Thead>
            <Tr>
              <Th>Title</Th>
              <Th>Date</Th>
              <Th>Service</Th>
              <Th>Environment</Th>
              <Th>Summary</Th>
              <Th>Recurrence</Th>
            </Tr>
          </Thead>
          <Tbody>
            {results.map((result) => (
              <Tr key={result.id}>
                <Td>
                  <Link to={`/incidents/${result.id}`} className="font-medium text-accent hover:underline">
                    {result.title}
                  </Link>
                </Td>
                <Td className="font-data">{formatTime(result.triggeredAt)}</Td>
                <Td>{result.service}</Td>
                <Td>{result.environment}</Td>
                <Td className="max-w-xs truncate">{result.analysisSummary ?? "—"}</Td>
                <Td className="font-data">{result.recurrenceCount}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </Card>
    </div>
  );
}
