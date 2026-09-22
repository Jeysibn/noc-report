import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ShiftReport } from "./ShiftReport";
import { __setCurrentUserForTests } from "@/lib/session";

const listReports = vi.fn();
const generateReport = vi.fn();
const getCurrentShift = vi.fn();
const openShift = vi.fn();

vi.mock("@/services", () => ({
  incidentService: {
    list: async () => ({ items: [] }),
  },
  shiftService: {
    getCurrentShift: () => getCurrentShift(),
    openShift: () => openShift(),
  },
  reportService: {
    list: (...args: unknown[]) => listReports(...(args as [string])),
    generate: (...args: unknown[]) =>
      generateReport(...(args as [string, unknown])),
    getDownloadUrl: async () => "https://example.test/download",
  },
  evidenceService: {},
  analysisService: {},
}));

beforeEach(() => {
  getCurrentShift.mockReset().mockResolvedValue({
    id: "shift-1",
    type: "day",
    startsAt: new Date().toISOString(),
    endsAt: new Date().toISOString(),
  });
  openShift.mockReset();
  listReports.mockReset().mockResolvedValue([]);
  generateReport.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ShiftReport", () => {
  it("renders incident readiness and lets a report be generated", async () => {
    generateReport.mockResolvedValue({
      id: "report-1",
      shiftId: "shift-1",
      snapshotId: "snap-1",
      jobId: "job-1",
      version: 1,
      status: "QUEUED",
      model: "historical-runtime",
      effort: null,
      skillName: "daily-alert-report",
      skillVersion: "1",
      generatedBy: null,
      generatedAt: null,
      errorMessage: null,
      createdAt: new Date().toISOString(),
      downloadable: false, previewable: false, reportVersionId: null,
    });

    render(
      <MemoryRouter>
        <ShiftReport />
      </MemoryRouter>,
    );

    await act(async () => {});
    expect(screen.getByText(/incident readiness/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /generate report/i }));
    await act(async () => {});

    expect(generateReport).toHaveBeenCalledWith("shift-1", {});
    expect(screen.getByText(/version 1/i)).toBeInTheDocument();
    expect(screen.getAllByText(/queued/i).length).toBeGreaterThan(0);
  });

  it("requests report generation without provider-specific selectors", async () => {
    generateReport.mockResolvedValue({
      id: "report-2", shiftId: "shift-1", snapshotId: "snap-2", jobId: "job-2",
      version: 1, status: "QUEUED", model: "historical-runtime", effort: null,
      skillName: "daily-alert-report", skillVersion: "1", generatedBy: null,
      generatedAt: null, errorMessage: null, createdAt: new Date().toISOString(), downloadable: false, previewable: false, reportVersionId: null,
    });
    render(<MemoryRouter><ShiftReport /></MemoryRouter>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /generate report/i }));
    await act(async () => {});
    expect(generateReport).toHaveBeenCalledWith("shift-1", {});
  });

  it("shows a download button once a report is downloadable", async () => {
    listReports.mockResolvedValue([
      {
        id: "report-1",
        shiftId: "shift-1",
        snapshotId: "snap-1",
        jobId: "job-1",
        version: 1,
        status: "COMPLETED",
        model: "historical-runtime",
        effort: "medium",
        skillName: "daily-alert-report",
        skillVersion: "1",
        generatedBy: "operator1",
        generatedAt: new Date().toISOString(),
        errorMessage: null,
        createdAt: new Date().toISOString(),
      downloadable: true, previewable: false, reportVersionId: "version-1",
      },
    ]);

    render(
      <MemoryRouter>
        <ShiftReport />
      </MemoryRouter>,
    );

    await act(async () => {});
    expect(
      screen.getByRole("button", { name: /download/i }),
    ).toBeInTheDocument();
  });

  it("allows read-only preview without requiring download permission", async () => {
    __setCurrentUserForTests({
      id: "reader",
      username: "reader",
      displayName: "Read Only",
      email: null,
      roles: ["NOC"],
      permissions: ["report.read"],
    });
    listReports.mockResolvedValue([
      {
        id: "report-preview",
        shiftId: "shift-1",
        snapshotId: "snap-1",
        jobId: "job-1",
        version: 1,
        status: "COMPLETED",
        model: "historical-runtime",
        effort: "low",
        skillName: "daily-alert-report",
        skillVersion: "1",
        generatedBy: null,
        generatedAt: new Date().toISOString(),
        errorMessage: null,
        createdAt: new Date().toISOString(),
        downloadable: false,
        previewable: true,
        reportVersionId: null,
      },
    ]);

    render(<MemoryRouter><ShiftReport /></MemoryRouter>);
    expect(await screen.findByRole("button", { name: /preview/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("distinguishes report history failure from a valid empty history", async () => {
    listReports.mockRejectedValueOnce(new Error("report API unavailable"));

    render(<MemoryRouter><ShiftReport /></MemoryRouter>);
    expect(await screen.findByText(/unable to load report history/i)).toBeInTheDocument();
    expect(screen.queryByText(/no reports generated yet/i)).not.toBeInTheDocument();
  });

  it("lets a NOC operator open a shift when none is active", async () => {
    __setCurrentUserForTests({
      id: "noc-user",
      username: "operator",
      displayName: "NOC Operator",
      email: null,
      roles: ["NOC"],
      permissions: ["shift.operate"],
    });
    getCurrentShift.mockResolvedValue(null);
    openShift.mockResolvedValue({
      id: "shift-2",
      type: "day",
      startsAt: new Date().toISOString(),
      endsAt: new Date().toISOString(),
      status: "active",
    });

    render(
      <MemoryRouter>
        <ShiftReport />
      </MemoryRouter>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /open shift/i }));
    await act(async () => {});

    expect(openShift).toHaveBeenCalledOnce();
    expect(screen.getByText(/day shift/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open shift/i })).not.toBeInTheDocument();
  });
});
