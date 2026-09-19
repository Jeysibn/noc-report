import { useEffect, useRef, useState } from "react";
import { Card, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Form";
import { StatusPill } from "@/components/ui/StatusPill";
import { analysisService } from "@/services";
import { hasPermission } from "@/lib/session";
import { ApiError } from "@/lib/http";
import type {
  AnalysisFind,
  AnalysisJobStatus,
  AnalysisRun,
} from "@/types/analysis";

const POLL_INTERVAL_MS = 3000;
const IN_FLIGHT: AnalysisJobStatus[] = ["QUEUED", "PROCESSING"];

const statusPill: Record<
  AnalysisJobStatus,
  { label: string; status: "neutral" | "info" | "good" | "critical" }
> = {
  NOT_ANALYZED: { label: "Not analyzed", status: "neutral" },
  QUEUED: { label: "Queued", status: "info" },
  PROCESSING: { label: "Processing", status: "info" },
  COMPLETED: { label: "Completed", status: "good" },
  FAILED: { label: "Failed", status: "critical" },
};

/**
 * Milestone 13 (Real Log Triage): replaces the old client-only jobSimulator
 * with the real pipeline — POST /incidents/{id}/analysis-runs enqueues a
 * real job, then this polls GET .../analysis-runs/{jobId} until the bridge
 * (a separate process) marks it COMPLETED/FAILED. No fallback analyzer: if
 * nothing ever picks the job up, this just stays QUEUED, truthfully.
 */
export function LogAnalysisPanel({
  incidentId,
  hasLog,
}: {
  incidentId?: string;
  hasLog: boolean;
}) {
  const [model, setModel] = useState<string | undefined>(undefined);
  const [effort, setEffort] = useState<"low" | "medium" | "high" | undefined>(undefined);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [historyState, setHistoryState] = useState<"loading" | "loaded" | "failed">("loading");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [pollStale, setPollStale] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const canAnalyze = hasPermission("incident.analysis.execute");

  useEffect(() => {
    let cancelled = false;

    if (!incidentId) {
      setHistoryState("loaded");
      return () => {
        cancelled = true;
      };
    }

    setHistoryState("loading");
    setHistoryError(null);
    setPollStale(false);
    // Do not briefly show a run belonging to the previous incident while the
    // newly selected incident's history is loading.
    setRuns([]);
    analysisService
      .listRuns(incidentId)
      .then((nextRuns) => {
        if (cancelled) return;
        setRuns(nextRuns);
        setHistoryState("loaded");
      })
      .catch((err) => {
        if (cancelled) return;
        setHistoryState("failed");
        setHistoryError(
          err instanceof ApiError
            ? err.message
            : "Unable to load analysis status.",
        );
      });
    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [incidentId, historyRetry]);

  const currentRun = runs.find((r) => r.isCurrent);

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (
      historyState !== "loaded" ||
      !incidentId ||
      !currentRun ||
      !IN_FLIGHT.includes(currentRun.status)
    )
      return;

    pollRef.current = setInterval(() => {
      analysisService
        .getRun(incidentId, currentRun.jobId)
        .then((updated) => {
          setPollStale(false);
          setRuns((prev) =>
            prev.map((r) => (r.jobId === updated.jobId ? updated : r)),
          );
        })
        .catch(() => {
          // Keep the last-known-good run visible. A transient poll failure is
          // not evidence that the analysis disappeared or is NOT_ANALYZED.
          setPollStale(true);
        });
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyState, incidentId, currentRun?.jobId, currentRun?.status]);

  function retryHistory() {
    setHistoryRetry((value) => value + 1);
  }

  async function requestAnalysis() {
    if (!incidentId) return;
    setRequestError(null);
    try {
      const run = await analysisService.requestAnalysis(incidentId, {
        model,
        effort,
      });
      setRuns((prev) => [
        run,
        ...prev.map((r) => ({ ...r, isCurrent: false })),
      ]);
    } catch (err) {
      setRequestError(
        err instanceof ApiError ? err.message : "Could not request analysis.",
      );
    }
  }

  const status: AnalysisJobStatus = currentRun?.status ?? "NOT_ANALYZED";
  const { label, status: pillStatus } = statusPill[status];
  const isBusy = IN_FLIGHT.includes(status);

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <CardTitle>Log Analysis</CardTitle>
        {historyState === "loaded" && <StatusPill status={pillStatus} label={label} />}
      </div>

      {historyState === "loading" && incidentId && (
        <p className="text-sm text-muted">Loading analysis status…</p>
      )}

      {historyState === "failed" && (
        <div className="rounded-lg bg-critical-tint p-3 text-sm text-critical">
          <p>
            {historyError ?? "Unable to load analysis status."}
          </p>
          <Button className="mt-3" size="sm" variant="secondary" onClick={retryHistory}>
            Retry
          </Button>
        </div>
      )}

      {pollStale && historyState === "loaded" && (
        <p className="text-xs text-warning" role="status">
          Analysis status temporarily unavailable — retrying…
        </p>
      )}

      {!hasLog && (
        <p className="text-sm text-muted">
          No log attached — attach one to request analysis.
        </p>
      )}

      {hasLog && !canAnalyze && (
        <p className="text-sm text-muted">
          You don&apos;t have permission to request log analysis.
        </p>
      )}

      {hasLog && canAnalyze && (
        <>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium">Model</label>
              <Select
                value={model ?? ""}
                onChange={(e) => setModel(e.target.value || undefined)}
                disabled={isBusy}
              >
                <option value="">System default / Auto</option>
                <option value="claude-sonnet-5">Claude Sonnet 5</option>
                <option value="claude-opus-5">Claude Opus 5</option>
              </Select>
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium">Effort</label>
              <Select
                value={effort ?? ""}
                onChange={(e) => setEffort((e.target.value || undefined) as typeof effort)}
                disabled={isBusy}
              >
                <option value="">System default / Auto</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </Select>
            </div>
          </div>

          <Button onClick={requestAnalysis} disabled={isBusy}>
            {isBusy ? "Analyzing..." : "Analyze log"}
          </Button>

          {requestError && (
            <p className="text-sm text-critical">{requestError}</p>
          )}

          {currentRun?.status === "COMPLETED" && currentRun.result && (
            <div className="rounded-lg bg-ground p-4 text-sm">
              {typeof currentRun.result.totalEntries === "number" && (
                <FindingCoverage result={currentRun.result} />
              )}
              <p className="flex items-center gap-2 font-medium">
                Severity
                <StatusPill
                  status={
                    currentRun.result.severitySignal === "critical" ||
                    currentRun.result.severitySignal === "high"
                      ? "critical"
                      : currentRun.result.severitySignal === "medium"
                        ? "warning"
                        : "good"
                  }
                  label={currentRun.result.severitySignal}
                />
              </p>

              <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <p className="font-medium">English</p>
                  <p className="mt-1 text-muted">
                    {currentRun.result.summaryEn}
                  </p>
                  <FindList
                    title="Key Finds"
                    finds={currentRun.result.keyFinds}
                    lang="en"
                  />
                  <FindList
                    title="Secondary Finds"
                    finds={currentRun.result.secondaryFinds}
                    lang="en"
                  />
                  <p className="mt-3 font-medium">Likely cause</p>
                  <p className="mt-1 text-muted">
                    {currentRun.result.likelyCauseEn}
                  </p>
                  <p className="mt-3 font-medium">Recommended action</p>
                  <p className="mt-1 text-muted">
                    {currentRun.result.recommendedActionEn}
                  </p>
                </div>
                <div>
                  <p className="font-medium">中文 (Chinese)</p>
                  <p className="mt-1 text-muted">
                    {currentRun.result.summaryZh}
                  </p>
                  <FindList
                    title="重点发现 (Key Finds)"
                    finds={currentRun.result.keyFinds}
                    lang="zh"
                  />
                  <FindList
                    title="次要发现 (Secondary Finds)"
                    finds={currentRun.result.secondaryFinds}
                    lang="zh"
                  />
                  <p className="mt-3 font-medium">最可能原因</p>
                  <p className="mt-1 text-muted">
                    {currentRun.result.likelyCauseZh}
                  </p>
                  <p className="mt-3 font-medium">建议措施</p>
                  <p className="mt-1 text-muted">
                    {currentRun.result.recommendedActionZh}
                  </p>
                </div>
              </div>
            </div>
          )}

          {currentRun?.status === "FAILED" && (
            <p className="rounded-lg bg-critical-tint p-3 text-sm text-critical">
              {currentRun.errorMessage ?? "Analysis failed."}
            </p>
          )}

          {runs.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-medium">Run history</p>
              <ul className="flex flex-col gap-2">
                {runs.map((run) => (
                  <li
                    key={run.jobId}
                    className="flex items-center justify-between text-xs text-muted"
                  >
                    <span className="font-data">
                      {run.jobId.slice(0, 8)} · {run.model} · {run.effort}
                    </span>
                    <span className="flex items-center gap-2">
                      {run.isCurrent && (
                        <span className="text-accent">current</span>
                      )}
                      <StatusPill
                        status={statusPill[run.status].status}
                        label={statusPill[run.status].label}
                      />
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function FindingCoverage({ result }: { result: AnalysisRun["result"] }) {
  if (!result || typeof result.totalEntries !== "number") return null;

  const findings = [...result.keyFinds, ...result.secondaryFinds];
  const accounted = findings.reduce(
    (sum, find) => sum + (typeof find.count === "number" ? find.count : 0),
    0,
  );
  const deterministicTemplates = findings.reduce(
    (sum, find) =>
      sum +
      (find.labelEn.startsWith("Deterministic log template:") &&
      typeof find.count === "number"
        ? find.count
        : 0),
    0,
  );
  const unquantified = findings.reduce(
    (sum, find) =>
      sum +
      ((find.patternIds?.includes("other") || find.patternIds?.includes("unquantified")) &&
      typeof find.count === "number"
        ? find.count
        : 0),
    0,
  );
  const named = Math.max(0, accounted - deterministicTemplates - unquantified);
  const coverage = result.totalEntries
    ? Math.min(100, (accounted / result.totalEntries) * 100)
    : 100;
  const coverageLabel = `${accounted.toLocaleString()} / ${result.totalEntries.toLocaleString()} (${coverage.toFixed(2)}%)`;

  return (
    <div className="mb-3 rounded-md border border-line bg-surface px-3 py-2 text-xs text-muted">
      <p>Exact log entries analyzed: {result.totalEntries.toLocaleString()}</p>
      <p>Finding coverage: {coverageLabel}</p>
      {deterministicTemplates > 0 && (
        <p className="mt-1 text-muted">
          Named findings: {named.toLocaleString()} ({((named / result.totalEntries) * 100).toFixed(2)}%);{" "}
          deterministic templates: {deterministicTemplates.toLocaleString()} ({((deterministicTemplates / result.totalEntries) * 100).toFixed(2)}%).
        </p>
      )}
      {unquantified > 0 && (
        <p className="mt-1 text-warning">
          Unquantified findings: {unquantified.toLocaleString()} ({((unquantified / result.totalEntries) * 100).toFixed(2)}%).
        </p>
      )}
      {unquantified === 0 && accounted !== result.totalEntries && (
        <p className="mt-1 text-warning">
          {Math.max(0, result.totalEntries - accounted).toLocaleString()} entries have no validated finding count.
        </p>
      )}
    </div>
  );
}

function FindList({
  title,
  finds,
  lang,
}: {
  title: string;
  finds: AnalysisFind[];
  lang: "en" | "zh";
}) {
  if (finds.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="font-medium">{title}</p>
      <ol className="mt-1 list-decimal space-y-1.5 pl-5">
        {finds.map((find, i) => {
          const label = lang === "en" ? find.labelEn : find.labelZh;
          const detail = lang === "en" ? find.detailEn : find.detailZh;
          const stats =
            find.count !== null && find.percentage !== null
              ? ` (${find.count.toLocaleString()} / ${find.percentage}%)`
              : find.count !== null
                ? ` (${find.count.toLocaleString()})`
                : "";
          return (
            <li key={i} className="text-muted">
              <span className="font-medium text-ink">
                {label}
                {stats}
              </span>
              {detail && <span> — {detail}</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
