export type JobState = "QUEUED" | "STARTING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface JobSimulatorOptions {
  /** Force a specific terminal outcome, for deterministic tests/demos. */
  outcome?: "success" | "failure";
  onState?: (state: JobState) => void;
}

/**
 * Reusable async job simulator — Queued ~800ms -> Starting ~500ms ->
 * Running 3-8s -> Completed, per the UI Phase Plan's Mock job simulator
 * spec. Lets generic job/loading/error UX be built before RabbitMQ/runtime workers
 * exist (Milestones 11/12).
 */
export function runMockJob<T>(
  produceResult: () => T,
  { outcome = "success", onState }: JobSimulatorOptions = {},
): Promise<{ state: "COMPLETED"; result: T } | { state: "FAILED"; error: string }> {
  const emit = (state: JobState) => onState?.(state);

  return new Promise((resolve) => {
    emit("QUEUED");
    setTimeout(() => {
      emit("STARTING");
      setTimeout(
        () => {
          emit("RUNNING");
          const runningMs = 3000 + Math.random() * 5000;
          setTimeout(() => {
            if (outcome === "failure") {
              emit("FAILED");
              resolve({ state: "FAILED", error: "Runtime worker job failed." });
            } else {
              emit("COMPLETED");
              resolve({ state: "COMPLETED", result: produceResult() });
            }
          }, runningMs);
        },
        500,
      );
    }, 800);
  });
}

/** Forced test-case outcomes the plan calls out, for exercising every job/error UI path. */
export const JOB_TEST_CASES = [
  "successful_log_analysis",
  "failed_log_analysis",
  "successful_report_generation",
  "failed_report_generation",
  "runtime_unavailable",
  "rabbitmq_unavailable",
  "minio_unavailable",
] as const;
