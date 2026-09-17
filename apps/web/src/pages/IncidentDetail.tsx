import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { evidenceService, incidentService } from "@/services";
import type { Incident } from "@/types/domain";
import type { IncidentTimelineEvent } from "@/services/incident.service";
import type { EvidenceRecord } from "@/services/evidence.service";
import { Card, CardTitle } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Form";
import { incidentStatusMap, formatTime } from "@/lib/incidentStatus";
import { hasPermission } from "@/lib/session";
import { LogAnalysisPanel } from "@/components/incidents/LogAnalysisPanel";
import { ApiError } from "@/lib/http";

/**
 * Incident Detail. Timeline is fetched from GET /incidents/{id}/timeline — a real,
 * timestamped sequence derived from Incident/Evidence/Job rows (see
 * app/api/v1/routers/incidents.py's get_incident_timeline), not a static
 * guess based on incident.status/hasLog. It's polled lightly so it
 * reflects an analysis job completing (written directly to Postgres by
 * the bridge) without requiring a page reload.
 */
export function IncidentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<IncidentTimelineEvent[]>([]);
  const [timelineError, setTimelineError] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [evidenceLoading, setEvidenceLoading] = useState(true);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceBusyId, setEvidenceBusyId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canUpdate = hasPermission("incident.update");
  const canManageEvidence = hasPermission("incident.evidence.upload");
  const canDelete = hasPermission("incident.delete");

  const refresh = () => {
    if (!id) return;
    setLoadError(null);
    setNotFound(false);
    setPermissionDenied(false);
    incidentService.get(id).then((result) => {
      if (result) setIncident(result);
      else setNotFound(true);
    }).catch((error) => {
      if (error instanceof ApiError && error.status === 403) {
        setPermissionDenied(true);
      } else {
        setLoadError(error instanceof Error ? error.message : "Unable to load incident.");
      }
    });
  };

  useEffect(refresh, [id]);

  async function handleMarkRecovered() {
    if (!id) return;
    setBusy(true);
    setActionError(null);
    try {
      const updated = await incidentService.update(id, {
        status: "recovered",
        recoveredAt: new Date().toISOString(),
      });
      setIncident(updated);
      incidentService.getTimeline(id).then(setTimeline).catch(() => setTimelineError(true));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to mark recovered");
    } finally {
      setBusy(false);
    }
  }

  function startEditing() {
    if (!incident) return;
    setEditTitle(incident.title);
    setIsEditing(true);
  }

  async function handleSaveEdit() {
    if (!id) return;
    setBusy(true);
    setActionError(null);
    try {
      const updated = await incidentService.update(id, { title: editTitle });
      setIncident(updated);
      setIsEditing(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to save changes");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!id) return;
    if (!window.confirm("Delete this incident permanently? This cannot be undone.")) return;
    setBusy(true);
    setActionError(null);
    try {
      await incidentService.delete(id);
      navigate("/incidents");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to delete incident");
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!id) return;
    setEvidenceLoading(true);
    setEvidenceError(null);
    evidenceService.list(id).then(setEvidence).catch((error) => {
      setEvidenceError(error instanceof Error ? error.message : "Unable to load evidence.");
    }).finally(() => setEvidenceLoading(false));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const fetchTimeline = () => {
      incidentService.getTimeline(id).then((events) => {
        setTimeline(events);
        setTimelineError(false);
      }).catch(() => setTimelineError(true));
    };
    fetchTimeline();
    pollRef.current = setInterval(fetchTimeline, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [id]);

  async function downloadEvidence(item: EvidenceRecord) {
    setEvidenceBusyId(item.id);
    setEvidenceError(null);
    try {
      const url = await evidenceService.getDownloadUrl(item.id);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (error) {
      setEvidenceError(error instanceof Error ? error.message : "Unable to download evidence.");
    } finally {
      setEvidenceBusyId(null);
    }
  }

  async function removeEvidence(item: EvidenceRecord) {
    if (!window.confirm(`Remove ${item.originalFilename}?`)) return;
    setEvidenceBusyId(item.id);
    setEvidenceError(null);
    try {
      await evidenceService.delete(item.id);
      const refreshed = await evidenceService.list(id!);
      setEvidence(refreshed);
    } catch (error) {
      setEvidenceError(error instanceof Error ? error.message : "Unable to remove evidence.");
      try {
        setEvidence(await evidenceService.list(id!));
      } catch {
        // Preserve the original actionable error if a refresh also fails.
      }
    } finally {
      setEvidenceBusyId(null);
    }
  }

  if (loadError) {
    return <Card><p className="text-sm text-critical">Unable to load incident: {loadError}</p><Button className="mt-3" size="sm" onClick={refresh}>Retry</Button></Card>;
  }
  if (permissionDenied) {
    return <Card><p className="text-sm text-critical">You do not have permission to view this incident.</p></Card>;
  }
  if (notFound) {
    return (
      <Card>
        <p className="text-sm text-muted">No incident found with ID “{id}”.</p>
        <Link to="/incidents" className="mt-2 inline-block text-sm text-accent hover:underline">
          Back to incidents
        </Link>
      </Card>
    );
  }

  if (!incident) return <Card><p className="text-sm text-muted">Loading incident…</p></Card>;

  const { status, label } = incidentStatusMap[incident.status];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="font-data text-sm text-muted">{incident.displayId}</p>
          {isEditing ? (
            <div className="mt-1 flex items-center gap-2">
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="max-w-md"
                autoFocus
              />
              <Button size="sm" onClick={handleSaveEdit} disabled={busy}>
                Save
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setIsEditing(false)} disabled={busy}>
                Cancel
              </Button>
            </div>
          ) : (
            <h1 className="text-xl font-semibold">{incident.title}</h1>
          )}
        </div>
        <div className="flex items-center gap-2">
          <StatusPill status={status} label={label} />
          {canUpdate && !isEditing && (
            <Button size="sm" variant="secondary" onClick={startEditing} disabled={busy}>
              Edit
            </Button>
          )}
          {canUpdate && incident.status !== "recovered" && (
            <Button size="sm" variant="secondary" onClick={handleMarkRecovered} disabled={busy}>
              Mark recovered
            </Button>
          )}
          {canDelete && (
            <Button size="sm" variant="danger" onClick={handleDelete} disabled={busy}>
              Delete
            </Button>
          )}
        </div>
      </div>

      {actionError && <p className="text-sm text-critical">{actionError}</p>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardTitle>Overview</CardTitle>
          <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-muted">Service</dt>
              <dd>{incident.service}</dd>
            </div>
            <div>
              <dt className="text-muted">Environment</dt>
              <dd className="capitalize">{incident.environment}</dd>
            </div>
            <div>
              <dt className="text-muted">Triggered</dt>
              <dd className="font-data">{formatTime(incident.triggeredAt)}</dd>
            </div>
            <div>
              <dt className="text-muted">Analysis status</dt>
              <dd className="capitalize">{incident.analysisStatus.replace("_", " ")}</dd>
            </div>
          </dl>
        </Card>

        <Card>
          <CardTitle>Evidence</CardTitle>
          {evidenceLoading && <p className="mt-4 text-sm text-muted">Loading evidence…</p>}
          {evidenceError && <p className="mt-4 text-sm text-critical">Unable to load evidence: {evidenceError}</p>}
          {!evidenceLoading && !evidenceError && evidence.length === 0 && (
            <p className="mt-4 text-sm text-muted">No evidence attached yet.</p>
          )}
          {!evidenceLoading && !evidenceError && evidence.length > 0 && (
            <ul className="mt-4 flex flex-col gap-3">
              {evidence.map((item) => (
                <li key={item.id} className="rounded border border-line p-3 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{item.originalFilename}</p>
                      <p className="mt-1 text-xs text-muted">
                        {item.evidenceType.replace(/_/g, " ")} · {item.byteSize == null ? "size unknown" : `${item.byteSize.toLocaleString()} bytes`}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-muted">{item.lifecycleState === "PURGE_PENDING" ? "Removal pending" : "Available"}</span>
                  </div>
                  <div className="mt-2 flex gap-2">
                    {item.lifecycleState === "ACTIVE" && <Button size="sm" variant="secondary" onClick={() => downloadEvidence(item)} disabled={evidenceBusyId === item.id}>Download</Button>}
                    {item.lifecycleState === "ACTIVE" && canManageEvidence && <Button size="sm" variant="danger" onClick={() => removeEvidence(item)} disabled={evidenceBusyId === item.id}>Remove</Button>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <LogAnalysisPanel incidentId={incident.id} hasLog={incident.hasLog} />

      <Card>
        <CardTitle>Timeline</CardTitle>
        {timelineError && <p className="mt-4 text-sm text-muted">Timeline temporarily unavailable; retrying.</p>}
        {timeline.length === 0 ? (
          <p className="mt-4 text-sm text-muted">No timeline events yet.</p>
        ) : (
          <ol className="mt-4 flex flex-col gap-3">
            {timeline.map((event, index) => {
              const isFailure = event.eventType === "job_failed";
              return (
                <li key={`${event.eventType}-${event.occurredAt}-${index}`} className="flex items-start gap-3 text-sm">
                  <span
                    className={
                      isFailure ? "mt-1 h-2 w-2 shrink-0 rounded-full bg-critical" : "mt-1 h-2 w-2 shrink-0 rounded-full bg-accent"
                    }
                  />
                  <div>
                    <div className="flex items-baseline gap-2">
                      <span className="text-ink">{event.label}</span>
                      <span className="font-data text-xs text-muted">{formatTime(event.occurredAt)}</span>
                    </div>
                    {isFailure && event.detail?.error_message ? (
                      <p className="mt-1 text-xs text-muted">{String(event.detail.error_message)}</p>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Card>
    </div>
  );
}
