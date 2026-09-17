import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "./Dashboard";

const getDashboardSummary = vi.fn();
const listIncidents = vi.fn();

vi.mock("@/services", () => ({
  incidentService: {
    getDashboardSummary: (...args: unknown[]) => getDashboardSummary(...args),
    list: (...args: unknown[]) => listIncidents(...args),
  },
  shiftService: { getCurrentShift: vi.fn().mockResolvedValue(null) },
}));

vi.mock("@/lib/operationalHealth", () => ({
  useOperationalHealth: () => ({ data: null, loading: false, error: null }),
}));

describe("Dashboard", () => {
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
});
