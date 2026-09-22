import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { LogAnalysisPanel } from "./LogAnalysisPanel";

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

  it("requests real analysis and shows the result once polling observes COMPLETED", async () => {
    vi.useFakeTimers();

    requestAnalysis.mockResolvedValue({
      jobId: "job-1",
      status: "QUEUED",
      model: "claude-sonnet-5",
      effort: "medium",
      skillVersion: "1",
      queuedAt: new Date().toISOString(),
      completedAt: null,
      errorMessage: null,
      isCurrent: true,
    });
    getRun.mockResolvedValue({
      jobId: "job-1",
      status: "COMPLETED",
      model: "claude-sonnet-5",
      effort: "medium",
      skillVersion: "1",
      queuedAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
      errorMessage: null,
      isCurrent: true,
      result: {
        totalEntries: 2965,
        summaryEn: "One error found in the log excerpt.",
        summaryZh: "日志摘录中发现一个错误。",
        keyFinds: [
          {
            labelEn: "Unhandled exception",
            labelZh: "未处理的异常",
            count: 1,
            percentage: null,
            detailEn: "Found in the request path.",
            detailZh: "在请求路径中发现。",
          },
        ],
        secondaryFinds: [
          {
            labelEn: "Other log entries not separately classified",
            labelZh: "未单独分类的其他日志条目",
            count: 2964,
            percentage: 99.97,
            patternIds: ["other"],
            detailEn: "Exact remainder.",
            detailZh: "精确剩余。",
          },
        ],
        severitySignal: "high",
        confidence: 0.87,
      },
    });

    render(<LogAnalysisPanel incidentId="incident-1" hasLog />);
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: /analyze log/i }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(requestAnalysis).toHaveBeenCalledWith("incident-1", {});

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(getRun).toHaveBeenCalledWith("incident-1", "job-1");
    expect(screen.getByText(/exact log entries analyzed: 2,965/i)).toBeInTheDocument();
    expect(screen.getByText(/finding coverage: 2,965 \/ 2,965 \(100.00%\)/i)).toBeInTheDocument();
    expect(screen.getByText(/unquantified findings: 2,964 \(99\.97%\)/i)).toBeInTheDocument();
    expect(screen.getByText(/one error found/i)).toBeInTheDocument();
    expect(screen.getAllByText(/completed/i).length).toBeGreaterThan(0);
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
        model: "claude-sonnet-5",
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
        model: "claude-sonnet-5",
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
});
