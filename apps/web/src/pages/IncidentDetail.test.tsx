import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { IncidentDetail } from "./IncidentDetail";

describe("IncidentDetail", () => {
  it("renders overview and timeline for a known incident", async () => {
    render(
      <MemoryRouter initialEntries={["/incidents/INC-1042"]}>
        <Routes>
          <Route path="/incidents/:id" element={<IncidentDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Payment gateway timeout")).toBeInTheDocument();
    expect(screen.getByText("Timeline")).toBeInTheDocument();
  });

  it("shows a not-found message for an unknown incident", async () => {
    render(
      <MemoryRouter initialEntries={["/incidents/INC-9999"]}>
        <Routes>
          <Route path="/incidents/:id" element={<IncidentDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/no incident found/i)).toBeInTheDocument();
  });
});
