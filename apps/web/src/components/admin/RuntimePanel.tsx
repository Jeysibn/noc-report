import { Card, CardTitle } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { formatTime } from "@/lib/incidentStatus";
import type { RuntimeStatus } from "@/types/admin";

function pillStatus(value: string): "good" | "warning" | "critical" | "info" | "neutral" {
  if (value === "healthy") return "good";
  if (value === "degraded") return "warning";
  if (value === "unavailable") return "critical";
  if (value === "enabled") return "info";
  return "neutral";
}

function RuntimeState({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-2 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <StatusPill status={pillStatus(value)} label={value.replace(/_/g, " ")} />
    </div>
  );
}

export function RuntimePanel({
  status,
  error,
}: {
  status: RuntimeStatus | null;
  error: string | null;
}) {
  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>AI Runtime</CardTitle>
      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
      {status === null && !error && <p className="text-sm text-muted">Loading runtime status…</p>}
      {status && (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <section aria-label="Runtime state">
              <RuntimeState label="Runtime" value={status.runtime} />
              <RuntimeState label="API gate" value={status.api_gate} />
              <RuntimeState label="Hermes runtime" value={status.hermes.status} />
              <RuntimeState label="PostgreSQL" value={status.dependencies.postgresql?.status ?? "unknown"} />
              <RuntimeState label="RabbitMQ" value={status.dependencies.rabbitmq?.status ?? "unknown"} />
              <RuntimeState label="MinIO" value={status.dependencies.minio?.status ?? "unknown"} />
            </section>
            <section aria-label="Worker state">
              <RuntimeState label="Log Analysis" value={status.log_analysis.status} />
              <RuntimeState label="Log Worker" value={status.log_analysis.worker.status} />
              <p className="py-1 text-xs text-muted">
                Profile: {status.log_analysis.profile ?? "not required"} · Queue: {status.log_analysis.queue_depth ?? "unknown"} · DLQ: {status.log_analysis.dlq_depth ?? "unknown"}
              </p>
              <RuntimeState label="Daily Report AI" value={status.daily_report.status} />
              <RuntimeState label="Daily Worker" value={status.daily_report.worker.status} />
              <p className="py-1 text-xs text-muted">
                Profile: {status.daily_report.profile ?? "not required"} · Queue: {status.daily_report.queue_depth ?? "unknown"} · DLQ: {status.daily_report.dlq_depth ?? "unknown"}
              </p>
            </section>
          </div>
          <div className="border-t border-border pt-3 text-sm text-muted">
            {status.last_successful_job ? (
              <>
                Last successful job: {status.last_successful_job.job_type.replace(/_/g, " ")} · {status.last_successful_job.provider ?? "provider unknown"} / {status.last_successful_job.model ?? "model unknown"} · {formatTime(status.last_successful_job.completed_at)}
              </>
            ) : "No successful AI job recorded"}
            <span className="ml-2">Checked {formatTime(status.checked_at)}</span>
          </div>
        </>
      )}
    </Card>
  );
}
