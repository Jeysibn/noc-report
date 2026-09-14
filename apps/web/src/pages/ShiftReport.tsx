import { useEffect, useMemo, useRef, useState } from "react";
import { incidentService, reportService, shiftService } from "@/services";
import type { Incident, Shift } from "@/types/domain";
import type { ReportJobStatus, ReportRun } from "@/types/report";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { ReportPreview } from "@/components/ReportPreview";
import { Table, Thead, Tbody, Tr, Th, Td } from "@/components/ui/Table";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Form";
import { ApiError } from "@/lib/http";
import { hasPermission } from "@/lib/session";
import { formatTime } from "@/lib/incidentStatus";

type IncidentReadiness = "READY" | "ANALYSIS_REQUIRED" | "NO_LOG";

const readinessPill: Record<
  IncidentReadiness,
  { label: string; status: "good" | "warning" | "critical" }
> = {
  READY: { label: "Ready", status: "good" },
  ANALYSIS_REQUIRED: { label: "Analysis required", status: "warning" },
  NO_LOG: { label: "No log", status: "critical" },
};

const jobPill: Record<
  ReportJobStatus,
  { label: string; status: "neutral" | "info" | "good" | "critical" }
> = {
  QUEUED: { label: "Queued", status: "info" },
  PROCESSING: { label: "Generating", status: "info" },
  COMPLETED: { label: "Completed", status: "good" },
  FAILED: { label: "Failed", status: "critical" },
};

const IN_FLIGHT: ReportJobStatus[] = ["QUEUED", "PROCESSING"];
const POLL_INTERVAL_MS = 3000;

function readinessFor(incident: Incident): IncidentReadiness {
  if (!incident.hasLog) return "NO_LOG";
  return incident.analysisStatus === "completed"
    ? "READY"
    : "ANALYSIS_REQUIRED";
}

/**
 * Shift Report (Milestone 14: Real Daily Report). Readiness table stays
 * informational only — unlike the old mock, the real generate_report
 * endpoint doesn't orchestrate missing analyses first; it just freezes a
 * snapshot of whatever incident/analysis state exists right now. Skill is
 * fixed (Daily Alert Report). Generation is a real job: POST
 * /shifts/{id}/reports enqueues it, then this polls the report list until
 * the bridge marks it COMPLETED/FAILED, same pattern as LogAnalysisPanel.
 */
export function ShiftReport() {
  const [shift, setShift] = useState<Shift | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [model, setModel] = useState("auto");
  const [effort, setEffort] = useState<"auto" | "low" | "medium">("auto");
  const [versions, setVersions] = useState<ReportRun[]>([]);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const canGenerateReport = hasPermission("report.generate");
  const canDownload = hasPermission("report.download");
  const canManageShift = hasPermission("shift.operate");
  const [openingShift, setOpeningShift] = useState(false);

  useEffect(() => {
    shiftService
      .getCurrentShift()
      .then((s) => {
        setShift(s);
      })
      .catch((err) =>
        setRequestError(
          err instanceof ApiError
            ? err.message
            : "Could not load the current shift.",
        ),
      );
  }, []);

  const shiftId = shift?.id;

  useEffect(() => {
    if (!shiftId) {
      setIncidents([]);
      setVersions([]);
      return;
    }
    let cancelled = false;
    const refreshReadiness = () => {
      incidentService
        .list({ shiftId, limit: 0 })
        .then((page) => {
          if (!cancelled) setIncidents(page.items);
        })
        .catch((err) => {
          if (!cancelled) setRequestError(err instanceof ApiError ? err.message : "Could not load incidents.");
        });
    };
    refreshReadiness();
    reportService.list(shiftId).then((items) => {
      if (!cancelled) setVersions(items);
    });
    const readinessRefresh = setInterval(refreshReadiness, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(readinessRefresh);
    };
  }, [shiftId]);

  const readinessRows = useMemo(
    () =>
      incidents.map((incident) => ({
        incidentId: incident.id,
        title: incident.title,
        readiness: readinessFor(incident),
      })),
    [incidents],
  );

  const missingAnalysisCount = readinessRows.filter(
    (r) => r.readiness === "ANALYSIS_REQUIRED",
  ).length;
  const current = versions[0];
  const isBusy = current ? IN_FLIGHT.includes(current.status) : false;

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (!shift || !current || !IN_FLIGHT.includes(current.status)) return;

    pollRef.current = setInterval(() => {
      reportService.list(shift.id).then(setVersions);
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shift?.id, current?.id, current?.status]);

  async function openShift() {
    setRequestError(null);
    setOpeningShift(true);
    try {
      const s = await shiftService.openShift();
      setShift(s);
    } catch (err) {
      setRequestError(
        err instanceof ApiError ? err.message : "Could not open a shift.",
      );
    } finally {
      setOpeningShift(false);
    }
  }

  async function generateReport() {
    if (!shift) return;
    setRequestError(null);
    try {
      const run = await reportService.generate(shift.id, {
        ...(model === "auto" ? {} : { model }),
        ...(effort === "auto" ? {} : { effort }),
      });
      setVersions((prev) => [run, ...prev]);
    } catch (err) {
      setRequestError(
        err instanceof ApiError ? err.message : "Could not generate report.",
      );
    }
  }

  async function download(reportId: string) {
    try {
      // Fetched (with auth) through the API rather than navigated to a
      // presigned MinIO URL — window.open()-ing that URL silently fails
      // whenever the browser can't reach MinIO's host/port directly
      // (a forwarded dev URL, a different network, etc.), even though
      // the file is genuinely ready.
      const blob = await reportService.download(reportId);
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "shift-report.docx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      setRequestError(
        err instanceof ApiError
          ? err.message
          : "Could not download the report.",
      );
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Shift Report</h1>
        {shift && (
          <p className="text-sm text-muted capitalize">
            {shift.type} shift · {formatTime(shift.startsAt)} –{" "}
            {formatTime(shift.endsAt)}
          </p>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Incident readiness</CardTitle>
          {missingAnalysisCount > 0 && (
            <StatusPill
              status="warning"
              label={`${missingAnalysisCount} missing analysis`}
            />
          )}
        </CardHeader>
        <Table>
          <Thead>
            <Tr>
              <Th>Incident</Th>
              <Th>Title</Th>
              <Th>Readiness</Th>
            </Tr>
          </Thead>
          <Tbody>
            {readinessRows.map((row) => {
              const { label, status } = readinessPill[row.readiness];
              return (
                <Tr key={row.incidentId}>
                  <Td className="font-data">{row.incidentId}</Td>
                  <Td>{row.title}</Td>
                  <Td>
                    <StatusPill status={status} label={label} />
                  </Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="flex flex-col gap-4">
          <CardTitle>Generate report</CardTitle>
          {!shift && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted">
                There&apos;s no active shift right now, so a report can&apos;t
                be filed against one.
              </p>
              {canManageShift ? (
                <Button
                  size="sm"
                  onClick={openShift}
                  disabled={openingShift}
                  className="self-start"
                >
                  {openingShift ? "Opening..." : "Open shift"}
                </Button>
              ) : (
                <p className="text-sm text-muted">
                  Ask someone with shift-management permission to open one.
                </p>
              )}
            </div>
          )}
          {!canGenerateReport && (
            <p className="text-sm text-muted">
              You don&apos;t have permission to generate reports.
            </p>
          )}
          {canGenerateReport && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1.5 block text-sm font-medium">
                    Model
                  </label>
                  <Select
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    disabled={isBusy}
                  >
                    <option value="auto">System default / Auto</option>
                    <option value="claude-sonnet-5">Claude Sonnet 5</option>
                    <option value="claude-opus-5">Claude Opus 5</option>
                  </Select>
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium">
                    Effort
                  </label>
                  <Select
                    value={effort}
                    onChange={(e) => setEffort(e.target.value as typeof effort)}
                    disabled={isBusy}
                  >
                    <option value="auto">System default / Auto</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                  </Select>
                </div>
              </div>
              <p className="text-sm text-muted">
                Skill: Daily Alert Report (fixed)
              </p>
              {missingAnalysisCount > 0 && (
                <p className="text-sm text-warning">
                  {missingAnalysisCount} incident(s) have no analysis yet — the
                  report will note them as-is, it will not run those jobs first.
                </p>
              )}
              <div className="flex items-center gap-3">
                <Button onClick={generateReport} disabled={isBusy || !shift}>
                  {isBusy ? "Generating..." : "Generate report"}
                </Button>
                {current && (
                  <StatusPill
                    status={jobPill[current.status].status}
                    label={jobPill[current.status].label}
                  />
                )}
              </div>
              {requestError && (
                <p className="text-sm text-critical">{requestError}</p>
              )}
            </>
          )}
        </Card>

        <Card>
          <CardTitle>Version history</CardTitle>
          {versions.length === 0 && (
            <p className="mt-3 text-sm text-muted">No reports generated yet.</p>
          )}
          <ul className="mt-3 flex flex-col gap-3">
            {versions.map((v) => (
              <li
                key={v.id}
                className="flex items-center justify-between border-t border-border pt-3 text-sm"
              >
                <div>
                  <p className="font-medium">Version {v.version}</p>
                  <p className="font-data text-xs text-muted">
                    {v.generatedAt
                      ? formatTime(v.generatedAt)
                      : "not generated yet"}{" "}
                    · {v.model ?? "—"}/{v.effort ?? "—"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <StatusPill
                    status={jobPill[v.status].status}
                    label={jobPill[v.status].label}
                  />
                  {v.downloadable && canDownload && (
                    <>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() =>
                          setPreviewingId((id) => (id === v.id ? null : v.id))
                        }
                      >
                        {previewingId === v.id ? "Hide preview" : "Preview"}
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => download(v.id)}
                      >
                        Download
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {previewingId && (
        <Card>
          <CardTitle>Report preview</CardTitle>
          <div className="mt-3">
            <ReportPreview reportId={previewingId} />
          </div>
        </Card>
      )}
    </div>
  );
}
