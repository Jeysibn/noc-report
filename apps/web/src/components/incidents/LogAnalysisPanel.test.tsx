import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { LogAnalysisPanel } from "./LogAnalysisPanel";
import { ApiError } from "@/lib/http";

const listRuns = vi.fn();
const requestAnalysis = vi.fn();
const getRun = vi.fn();

vi.mock("@/services", () => ({
  incidentService: {},
  shiftService: {},
  evidenceService: {},
  analysisService: {
    listRuns: (...args: unknown[]) => listRuns(...(args as [string])),
    requestAnalysis: (...args: unknown[]) =>
      requestAnalysis(...(args as [string, unknown])),
    getRun: (...args: unknown[]) => getRun(...(args as [string, string])),
  },
}));

beforeEach(() => {
  listRuns.mockReset().mockResolvedValue([]);
  requestAnalysis.mockReset();
  getRun.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("LogAnalysisPanel", () => {
  it("shows a message instead of controls when no log is attached", () => {
    render(<LogAnalysisPanel hasLog={false} />);
    expect(screen.getByText(/no log attached/i)).toBeInTheDocument();
  });

  it("shows the unavailable state without creating or polling a job", async () => {
    requestAnalysis.mockRejectedValueOnce(
      new ApiError(503, "The analysis engine is not currently configured.", {
        detail: {
          code: "AI_RUNTIME_UNAVAILABLE",
          message: "The analysis engine is not currently configured.",
        },
      }),
    );
    render(<LogAnalysisPanel incidentId="incident-1" hasLog logFilename="sanitized-log.json" />);
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: /request analysis/i }));
    await act(async () => await Promise.resolve());
    expect(requestAnalysis).toHaveBeenCalledWith("incident-1", {});
    expect(await screen.findByText("The analysis engine is not currently configured.", { exact: true })).toBeInTheDocument();
    expect(getRun).not.toHaveBeenCalled();
  });

  it("does not mislabel an analysis-history load failure as not analyzed", async () => {
    listRuns.mockRejectedValueOnce(new Error("analysis history unavailable"));
    render(<LogAnalysisPanel incidentId="incident-1" hasLog />);

    expect(await screen.findByText(/unable to load analysis status/i)).toBeInTheDocument();
    expect(screen.queryByText("Not analyzed")).not.toBeInTheDocument();

    listRuns.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Not analyzed")).toBeInTheDocument();
  });

  it("preserves the last-known-good run while polling is stale and clears stale after recovery", async () => {
    vi.useFakeTimers();
    listRuns.mockResolvedValueOnce([
      {
        jobId: "job-1",
        status: "PROCESSING",
        model: "historical-runtime",
        effort: "medium",
        skillVersion: "1",
        queuedAt: new Date().toISOString(),
        completedAt: null,
        errorMessage: null,
        isCurrent: true,
      },
    ]);
    getRun
      .mockRejectedValueOnce(new Error("temporary poll failure"))
      .mockResolvedValueOnce({
        jobId: "job-1",
        status: "COMPLETED",
        model: "historical-runtime",
        effort: "medium",
        skillVersion: "1",
        queuedAt: new Date().toISOString(),
        completedAt: new Date().toISOString(),
        errorMessage: null,
        isCurrent: true,
      });

    render(<LogAnalysisPanel incidentId="incident-1" hasLog />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getAllByText(/processing/i).length).toBeGreaterThan(0);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByText(/analysis status temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.getAllByText(/processing/i).length).toBeGreaterThan(0);

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.queryByText(/analysis status temporarily unavailable/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/completed/i).length).toBeGreaterThan(0);
  });

  it("keeps a large secondary-find list compact until the operator expands it", async () => {
    const secondaryFinds = Array.from({ length: 10 }, (_, index) => ({
      id: `secondary-${index}`,
      labelEn: `Pattern ${index} (10)`,
      labelZh: `模式 ${index}（10）`,
      count: 10,
      percentage: 1.72,
      patternIds: [`p-${index}`],
      detailEn: `Observed pattern ${index}.`,
      detailZh: `检测到模式 ${index}。`,
    }));
    listRuns.mockResolvedValueOnce([{
      jobId: "job-complete",
      status: "COMPLETED",
      model: "hermes",
      effort: "medium",
      skillVersion: "1",
      queuedAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
      errorMessage: null,
      isCurrent: true,
      result: {
        totalEntries: 100,
        summaryEn: "Summary",
        summaryZh: "摘要",
        keyFinds: [],
        secondaryFinds,
        severitySignal: "high",
        confidence: 0.9,
      },
    }]);

    render(<LogAnalysisPanel incidentId="incident-1" hasLog />);
    await act(async () => await Promise.resolve());

    expect(screen.getAllByText(/Pattern 0/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Pattern 9/)).not.toBeInTheDocument();
    const expanders = screen.getAllByRole("button").filter((button) =>
      /show 2 more secondary findings|显示其余 2 个次要发现/i.test(button.textContent ?? ""),
    );
    expect(expanders).toHaveLength(2);

    fireEvent.click(expanders[1]);
    expect(screen.getAllByText(/Pattern 9/).length).toBeGreaterThan(0);
  });
});
