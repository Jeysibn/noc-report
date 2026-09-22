import { useEffect, useRef, useState } from "react";
import { Card, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
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

/** The UI exposes application analysis states; Hermes remains an infrastructure detail. */
export function LogAnalysisPanel({
  incidentId,
  hasLog,
  logFilename,
}: {
  incidentId?: string;
  hasLog: boolean;
  logFilename?: string;
}) {
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
      const run = await analysisService.requestAnalysis(incidentId, {});
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
          <p className="text-sm text-muted">
            Analysis engine is not currently configured. Historical results remain available below.
          </p>

          <Button onClick={requestAnalysis} disabled={isBusy}>
            {isBusy ? "Analyzing..." : "Request analysis"}
          </Button>

          {requestError && (
            <p className="text-sm text-critical">{requestError}</p>
          )}

          {currentRun?.status === "COMPLETED" && currentRun.result && (
            <div className="rounded-lg bg-ground p-4 text-sm">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line pb-3 text-xs text-muted">
                {logFilename && <span>Log File: {logFilename}</span>}
                {typeof currentRun.result.totalEntries === "number" && (
                  <span>Log entries: {currentRun.result.totalEntries.toLocaleString()}</span>
                )}
                <span className="flex items-center gap-2">
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
                </span>
              </div>

              <div className="mt-4 flex flex-col gap-5">
                <AnalysisLanguageSection result={currentRun.result} language="zh" />
                <AnalysisLanguageSection result={currentRun.result} language="en" />
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

function AnalysisLanguageSection({
  result,
  language,
}: {
  result: NonNullable<AnalysisRun["result"]>;
  language: "en" | "zh";
}) {
  const isChinese = language === "zh";
  return (
    <section className="flex flex-col gap-2" aria-labelledby={`analysis-${language}`}>
      <h3 id={`analysis-${language}`} className="text-base font-semibold text-accent">
        {isChinese ? "Chinese" : "English"}
      </h3>
      <h4 className="font-medium">Short Summary</h4>
      <p className="text-muted">{isChinese ? result.summaryZh : result.summaryEn}</p>
      <FindList title="Key Finds" finds={result.keyFinds} lang={language} />
      <FindList title="Secondary Finds" finds={result.secondaryFinds} lang={language} />
    </section>
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
  const ordered = title === "Key Finds";
  return (
    <div className="mt-3">
      <p className="font-medium">{title}</p>
      {finds.length > 0 && (ordered ? (
        <ol className="mt-1 list-decimal space-y-1.5 pl-5">
          {finds.map((find, i) => <FindingItem key={i} find={find} lang={lang} />)}
        </ol>
      ) : (
        <ul className="mt-1 list-disc space-y-1.5 pl-5">
          {finds.map((find, i) => <FindingItem key={i} find={find} lang={lang} compact />)}
        </ul>
      ))}
    </div>
  );
}

function FindingItem({ find, lang, compact = false }: { find: AnalysisFind; lang: "en" | "zh"; compact?: boolean }) {
  const label = lang === "en" ? find.labelEn : find.labelZh;
  const detail = lang === "en" ? find.detailEn : find.detailZh;
  const stats =
    find.count !== null && find.percentage !== null
      ? ` (${find.count.toLocaleString()} / ${find.percentage}%)`
      : find.count !== null
        ? ` (${find.count.toLocaleString()})`
        : "";
  return (
    <li className={compact ? "text-xs leading-5 text-muted" : "text-sm leading-6 text-muted"}>
      <span className="font-medium text-ink">
        {label}
        {stats}
      </span>
      {detail && <span> — {detail}</span>}
    </li>
  );
}
