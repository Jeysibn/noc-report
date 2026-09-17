import { useEffect, useState } from "react";
import { incidentService, shiftService } from "@/services";
import type { DashboardSummary, Incident, Shift } from "@/types/domain";
import { StatCard } from "@/components/ui/StatCard";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { Table, Thead, Tbody, Tr, Th, Td } from "@/components/ui/Table";
import { AlertTriangle, FileText, BarChart, Upload, Search } from "@/components/ui/icons";
import { incidentStatusMap, formatTime } from "@/lib/incidentStatus";
import { Link } from "react-router-dom";
import { useOperationalHealth } from "@/lib/operationalHealth";

function healthPill(status: "healthy" | "degraded" | "unavailable" | "unknown" | "disabled" | "not_applicable") {
  return {
    status: status === "healthy" ? "good" : status === "degraded" ? "warning" : status === "unavailable" ? "critical" : "neutral",
    label: status === "healthy" ? "Dependencies healthy" : status === "degraded" ? "Dependencies degraded" : status === "unavailable" ? "Dependencies unavailable" : "Dependencies unknown",
  } as const;
}

export function Dashboard() {
  const [shift, setShift] = useState<Shift | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [incidentsError, setIncidentsError] = useState<string | null>(null);
  const health = useOperationalHealth();
  const healthStatus = healthPill(health.data?.status ?? "unknown");

  function loadSummary() {
    setSummaryLoading(true);
    setSummaryError(null);
    incidentService.getDashboardSummary().then(setSummary).catch((error) => {
      setSummaryError(error instanceof Error ? error.message : "Unable to load dashboard metrics.");
    }).finally(() => setSummaryLoading(false));
  }

  useEffect(() => {
    shiftService.getCurrentShift().then(setShift);
    loadSummary();
    incidentService.list({ limit: 5 }).then((page) => {
      setIncidents(page.items);
      setIncidentsError(null);
    }).catch((error) => {
      setIncidentsError(error instanceof Error ? error.message : "Unable to load recent incidents.");
    });
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-sm text-muted">Current shift and NOC operational summary.</p>
        </div>
        <StatusPill status={healthStatus.status} label={healthStatus.label} />
      </div>

      {shift && (
        <Card className="flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted">Current shift</p>
            <p className="mt-1 text-lg font-semibold capitalize">{shift.type} shift</p>
            <p className="font-data text-sm text-muted">
              {formatTime(shift.startsAt)} – {formatTime(shift.endsAt)} · {shift.operator}
            </p>
          </div>
          <StatusPill status="good" label="Active" />
        </Card>
      )}

      {summaryLoading && <p className="text-sm text-muted">Loading operational metrics…</p>}
      {summaryError && (
        <Card>
          <p className="text-sm text-critical">Unable to load operational metrics: {summaryError}</p>
          <Button className="mt-3" size="sm" variant="secondary" onClick={loadSummary}>Retry</Button>
        </Card>
      )}
      {summary && !summaryError && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard
            label="Open incidents"
            value={String(summary.openIncidents)}
            icon={<AlertTriangle className="h-5 w-5" />}
            delta={{ label: `${summary.activeAlerts} active alerts`, direction: "up" }}
          />
          <StatCard
            label="Logs awaiting analysis"
            value={String(summary.logsAwaitingAnalysis)}
            icon={<FileText className="h-5 w-5" />}
            delta={{
              label: `${summary.analysesRunning} running`,
              direction: "flat",
            }}
          />
          <StatCard
            label="Reports generated this shift"
            value={String(summary.reportsGeneratedThisShift)}
            icon={<BarChart className="h-5 w-5" />}
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Recent incidents</CardTitle>
            <Link to="/incidents">
              <Button size="sm" variant="secondary">
                View all
              </Button>
            </Link>
          </CardHeader>
          {incidentsError ? (
            <p className="text-sm text-critical">Unable to load recent incidents: {incidentsError}</p>
          ) : <Table>
            <Thead>
              <Tr>
                <Th>ID</Th>
                <Th>Title</Th>
                <Th>Service</Th>
                <Th>Status</Th>
                <Th>Analysis</Th>
              </Tr>
            </Thead>
            <Tbody>
              {incidents.map((incident) => {
                const { status, label } = incidentStatusMap[incident.status];
                return (
                  <Tr key={incident.id}>
                    <Td className="font-data">
                      <Link to={`/incidents/${incident.id}`} className="text-accent hover:underline">
                        {incident.displayId}
                      </Link>
                    </Td>
                    <Td>{incident.title}</Td>
                    <Td className="text-muted">{incident.service}</Td>
                    <Td>
                      <StatusPill status={status} label={label} />
                    </Td>
                    <Td className="text-muted capitalize">
                      {incident.analysisStatus.replace("_", " ")}
                    </Td>
                  </Tr>
                );
              })}
            </Tbody>
          </Table>}
        </Card>

        <Card className="flex flex-col gap-3">
          <CardTitle>Quick actions</CardTitle>
          <Link to="/incidents/new">
            <Button variant="secondary" className="w-full justify-start">
              <AlertTriangle className="h-4 w-4" /> Create incident
            </Button>
          </Link>
          <Link to="/incidents/new">
            <Button variant="secondary" className="w-full justify-start">
              <Upload className="h-4 w-4" /> Upload screenshot
            </Button>
          </Link>
          <Link to="/reports">
            <Button variant="secondary" className="w-full justify-start">
              <FileText className="h-4 w-4" /> View shift report
            </Button>
          </Link>
          <Link to="/knowledge">
            <Button variant="secondary" className="w-full justify-start">
              <Search className="h-4 w-4" /> Search knowledge base
            </Button>
          </Link>
        </Card>
      </div>
    </div>
  );
}
