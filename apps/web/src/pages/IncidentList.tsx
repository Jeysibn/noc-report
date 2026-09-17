import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { incidentService } from "@/services";
import type { Incident } from "@/types/domain";
import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { Table, Thead, Tbody, Tr, Th, Td } from "@/components/ui/Table";
import { Select } from "@/components/ui/Form";
import { Button } from "@/components/ui/Button";
import { incidentStatusMap, formatTime } from "@/lib/incidentStatus";

const PAGE_SIZE = 20;

/** Filters live in URL query state so filtered views remain shareable. */
export function IncidentList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [total, setTotal] = useState(0);

  const status = searchParams.get("status") ?? "";
  const hasLog = searchParams.get("hasLog") ?? "";
  const page = Number(searchParams.get("page") ?? "1");

  useEffect(() => {
    incidentService
      .list({
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        status: status ? (status as Incident["status"]) : undefined,
        hasLog: hasLog ? hasLog === "true" : undefined,
      })
      .then((result) => {
        setIncidents(result.items);
        setTotal(result.total);
      });
  }, [status, hasLog, page]);

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    setSearchParams(next);
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Incidents</h1>
        <p className="text-sm text-muted">{total} incidents matching current filters.</p>
      </div>

      <Card className="flex flex-wrap items-end gap-4">
        <div>
          <label className="mb-1.5 block text-sm font-medium">Status</label>
          <Select value={status} onChange={(e) => updateFilter("status", e.target.value)}>
            <option value="">All</option>
            <option value="open">Open</option>
            <option value="investigating">Investigating</option>
            <option value="recovered">Recovered</option>
          </Select>
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">Has log</label>
          <Select value={hasLog} onChange={(e) => updateFilter("hasLog", e.target.value)}>
            <option value="">All</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </Select>
        </div>
      </Card>

      <Card>
        <Table>
          <Thead>
            <Tr>
              <Th>ID</Th>
              <Th>Triggered</Th>
              <Th>Title</Th>
              <Th>Service</Th>
              <Th>Environment</Th>
              <Th>Status</Th>
              <Th>Log</Th>
              <Th>Analysis</Th>
            </Tr>
          </Thead>
          <Tbody>
            {incidents.map((incident) => {
              const { status: pillStatus, label } = incidentStatusMap[incident.status];
              return (
                <Tr key={incident.id}>
                  <Td className="font-data">
                    <Link to={`/incidents/${incident.id}`} className="text-accent hover:underline">
                      {incident.displayId}
                    </Link>
                  </Td>
                  <Td className="font-data text-muted">{formatTime(incident.triggeredAt)}</Td>
                  <Td>{incident.title}</Td>
                  <Td className="text-muted">{incident.service}</Td>
                  <Td className="text-muted">{incident.environment}</Td>
                  <Td>
                    <StatusPill status={pillStatus} label={label} />
                  </Td>
                  <Td className="text-muted">{incident.hasLog ? "Yes" : "No"}</Td>
                  <Td className="text-muted capitalize">{incident.analysisStatus.replace("_", " ")}</Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>

        <div className="mt-4 flex items-center justify-between">
          <p className="text-sm text-muted">
            Page {page} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={page <= 1}
              onClick={() => updateFilter("page", String(page - 1))}
            >
              Previous
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => updateFilter("page", String(page + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
