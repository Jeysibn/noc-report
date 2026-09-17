import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Analytics } from "./Analytics";
import { __setCurrentUserForTests } from "@/lib/session";
import { analyticsService } from "@/services";

describe("Analytics", () => {
  it("renders V1 chart sections", async () => {
    __setCurrentUserForTests({
      id: "test-user",
      username: "admin",
      displayName: "Admin User",
      email: null,
      roles: ["Admin"],
      permissions: [],
    });
    render(<Analytics />);
    await act(async () => {});
    expect(screen.getByText(/incidents per day/i)).toBeInTheDocument();
    expect(screen.getByText(/recovered vs unresolved/i)).toBeInTheDocument();
    expect(screen.getByText(/top recurring alert titles/i)).toBeInTheDocument();
  });

  it("shows an explicit failure and retry instead of loading forever", async () => {
    const getSummary = vi.spyOn(analyticsService, "getSummary")
      .mockRejectedValueOnce(new Error("analytics unavailable"))
      .mockResolvedValueOnce({
        incidentsByDay: [], incidentsByShift: [], alertsByService: [],
        recoveredVsUnresolved: [], analysisJobOutcomes: [],
        topRecurringAlertTitles: [],
        reportGenerationCounts: { thisShift: 0, today: 0, thisWeek: 0 },
      });
    render(<Analytics />);
    expect(await screen.findByText(/unable to load analytics/i)).toBeInTheDocument();
    expect(screen.queryByText(/loading analytics/i)).not.toBeInTheDocument();
    await act(async () => {
      screen.getByRole("button", { name: /retry/i }).click();
    });
    expect(await screen.findByText(/incidents per day/i)).toBeInTheDocument();
    getSummary.mockRestore();
  });
});
