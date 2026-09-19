import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "./Dashboard";

const getDashboardSummary = vi.fn();
const listIncidents = vi.fn();
const getCurrentShift = vi.fn();

vi.mock("@/services", () => ({
  incidentService: {
    getDashboardSummary: (...args: unknown[]) => getDashboardSummary(...args),
    list: (...args: unknown[]) => listIncidents(...args),
  },
  shiftService: { getCurrentShift: (...args: unknown[]) => getCurrentShift(...args) },
}));

vi.mock("@/lib/operationalHealth", () => ({
  useOperationalHealth: () => ({ data: null, loading: false, error: null }),
}));

describe("Dashboard", () => {
  beforeEach(() => {
    getCurrentShift.mockReset().mockResolvedValue(null);
    getDashboardSummary.mockReset();
    listIncidents.mockReset();
  });

  it("renders aggregate metrics and only factual status text", async () => {
    getDashboardSummary.mockResolvedValue({
      openIncidents: 301,
      activeAlerts: 4,
      recoveredAlerts: 2,
      logsAwaitingAnalysis: 7,
      analysesRunning: 1,
      reportsGeneratedThisShift: 3,
    });
    listIncidents.mockResolvedValue({ items: [], total: 301 });

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    expect(await screen.findByText("301")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(screen.queryByText(/on track/i)).not.toBeInTheDocument();
  });

  it("shows a recent-incident load failure instead of an empty table", async () => {
    getDashboardSummary.mockResolvedValue({
      openIncidents: 0, activeAlerts: 0, recoveredAlerts: 0,
      logsAwaitingAnalysis: 0, analysesRunning: 0, reportsGeneratedThisShift: 0,
    });
    listIncidents.mockRejectedValue(new Error("recent incidents unavailable"));

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    expect(await screen.findByText(/unable to load recent incidents/i)).toBeInTheDocument();
  });

  it("distinguishes current-shift load failure from no active shift and supports retry", async () => {
    getDashboardSummary.mockResolvedValue({
      openIncidents: 0, activeAlerts: 0, recoveredAlerts: 0,
      logsAwaitingAnalysis: 0, analysesRunning: 0, reportsGeneratedThisShift: 0,
    });
    listIncidents.mockResolvedValue({ items: [], total: 0 });
    getCurrentShift
      .mockRejectedValueOnce(new Error("shift service unavailable"))
      .mockResolvedValueOnce(null);

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    expect(await screen.findByText(/unable to load current shift/i)).toBeInTheDocument();
    expect(screen.getByText(/shift service unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText("No active shift.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No active shift.")).toBeInTheDocument();
    expect(screen.queryByText(/unable to load current shift/i)).not.toBeInTheDocument();
  });
});
