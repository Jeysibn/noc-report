import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { incidentService, reportService, shiftService } from "@/services";
import type { Incident, Shift } from "@/types/domain";
import type { ReportJobStatus, ReportRun } from "@/types/report";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { ReportPreview } from "@/components/ReportPreview";
import { Table, Thead, Tbody, Tr, Th, Td } from "@/components/ui/Table";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/lib/http";
import { hasPermission } from "@/lib/session";
import { formatTime } from "@/lib/incidentStatus";

type IncidentReadiness = "READY" | "ANALYSIS_REQUIRED" | "NO_LOG";

const readinessPill: Record<
  IncidentReadiness,
  { label: string; status: "good" | "warning" | "info" }
> = {
  READY: { label: "Ready", status: "good" },
  ANALYSIS_REQUIRED: { label: "Analysis required", status: "warning" },
  NO_LOG: { label: "No log attached", status: "info" },
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
 * Shift Report. Readiness table stays informational; the real generate_report
 * endpoint doesn't orchestrate missing analyses first; it just freezes a
 * snapshot of whatever incident/analysis state exists right now. Skill is
 * fixed (Daily Alert Report). Generation is a real job: POST
 * /shifts/{id}/reports enqueues it, then this polls the report list until
 * a runtime worker marks it COMPLETED/FAILED, same pattern as LogAnalysisPanel.
 */
export function ShiftReport() {
  const [shift, setShift] = useState<Shift | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [versions, setVersions] = useState<ReportRun[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [versionsStale, setVersionsStale] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const shiftId = shift?.id;

  const canGenerateReport = hasPermission("report.generate");
  const canReadReport = hasPermission("report.read");
  const canDownload = hasPermission("report.download");
  const canManageShift = hasPermission("shift.operate");
  const [openingShift, setOpeningShift] = useState(false);

  const refreshReportHistory = useCallback(
    (showLoading = false) => {
      if (!shiftId) return;
      if (showLoading) setVersionsLoading(true);
      reportService
        .list(shiftId)
        .then((items) => {
          setVersions(items);
          setVersionsError(null);
          setVersionsStale(false);
        })
        .catch((err) => {
          setVersionsError(
            err instanceof ApiError
              ? err.message
              : "Could not load report history.",
          );
          setVersionsStale(true);
        })
        .finally(() => {
          if (showLoading) setVersionsLoading(false);
        });
    },
    [shiftId],
  );

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

  useEffect(() => {
    if (!shiftId) {
      setIncidents([]);
      setVersions([]);
      setVersionsError(null);
      setVersionsStale(false);
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
    refreshReportHistory(true);
    const readinessRefresh = setInterval(refreshReadiness, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(readinessRefresh);
    };
  }, [shiftId, refreshReportHistory]);

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
      refreshReportHistory(false);
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shift?.id, current?.id, current?.status, refreshReportHistory]);

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
      const run = await reportService.generate(shift.id, {});
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
              <p className="text-sm text-muted">
                Analysis engine is not currently configured. Historical reports remain available below.
              </p>
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
          {versionsLoading && (
            <p className="mt-3 text-sm text-muted">Loading report history…</p>
          )}
          {versionsError && (
            <div className="mt-3 rounded-md border border-border p-3">
              <p className="text-sm text-critical">
                Unable to load report history: {versionsError}
              </p>
              <Button
                className="mt-3"
                size="sm"
                variant="secondary"
                onClick={() => refreshReportHistory(true)}
              >
                Retry
              </Button>
            </div>
          )}
          {versionsStale && !versionsError && (
            <p className="mt-3 text-xs text-warning">
              Report history may be stale; retrying…
            </p>
          )}
          {!versionsLoading && !versionsError && versions.length === 0 && (
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
                  {v.previewable && canReadReport && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() =>
                        setPreviewingId((id) => (id === v.id ? null : v.id))
                      }
                    >
                      {previewingId === v.id ? "Hide preview" : "Preview"}
                    </Button>
                  )}
                  {v.downloadable && canDownload && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => download(v.id)}
                    >
                      Download
                    </Button>
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
