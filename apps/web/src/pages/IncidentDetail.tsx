import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { incidentService } from "@/services";
import type { Incident } from "@/types/domain";
import type { IncidentTimelineEvent } from "@/services/incident.service";
import { Card, CardTitle } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Form";
import { incidentStatusMap, formatTime } from "@/lib/incidentStatus";
import { hasPermission } from "@/lib/session";
import { LogAnalysisPanel } from "@/components/incidents/LogAnalysisPanel";

/**
 * Incident Detail (Milestone 3, Timeline re-wired later to real data).
 * Overview + evidence placeholder. Log Analysis/Reports/Audit sections
 * land with their own milestones (5, 6, 7) — this page's job is the shell
 * + Overview + Timeline + Evidence per the UI Phase Plan.
 *
 * Timeline is fetched from GET /incidents/{id}/timeline — a real,
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
  const [timeline, setTimeline] = useState<IncidentTimelineEvent[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canUpdate = hasPermission("incident.update");
  const canDelete = hasPermission("incident.delete");

  const refresh = () => {
    if (!id) return;
    incidentService.get(id).then((result) => {
      if (result) setIncident(result);
      else setNotFound(true);
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
      incidentService.getTimeline(id).then(setTimeline).catch(() => {});
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
    const fetchTimeline = () => {
      incidentService.getTimeline(id).then(setTimeline).catch(() => {});
    };
    fetchTimeline();
    pollRef.current = setInterval(fetchTimeline, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [id]);

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

  if (!incident) return null;

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
          {incident.hasLog ? (
            <p className="mt-4 text-sm text-muted">1 log attachment, 0 screenshots (mock).</p>
          ) : (
            <p className="mt-4 text-sm text-muted">No evidence attached yet.</p>
          )}
        </Card>
      </div>

      <LogAnalysisPanel incidentId={incident.id} hasLog={incident.hasLog} />

      <Card>
        <CardTitle>Timeline</CardTitle>
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
