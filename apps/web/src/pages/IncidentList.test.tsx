import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { IncidentList } from "./IncidentList";

describe("IncidentList", () => {
  it("renders a page of incidents with total count", async () => {
    render(
      <MemoryRouter initialEntries={["/incidents"]}>
        <IncidentList />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/120 incidents/i)).toBeInTheDocument();
    expect(screen.getByText("INC-1042")).toBeInTheDocument();
  });
});
