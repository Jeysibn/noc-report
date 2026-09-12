import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CreateIncident } from "./CreateIncident";

describe("CreateIncident", () => {
  it("disables save until a title is entered, then enables it", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CreateIncident />
      </MemoryRouter>,
    );

    const saveButton = screen.getByRole("button", { name: /save incident/i });
    expect(saveButton).toBeDisabled();

    await user.type(screen.getByLabelText(/^title$/i), "Test incident");
    expect(saveButton).toBeEnabled();
  });

  it("shows the OCR upload zone before any screenshot is provided", () => {
    render(
      <MemoryRouter>
        <CreateIncident />
      </MemoryRouter>,
    );
    expect(screen.getByText(/drop a screenshot/i)).toBeInTheDocument();
  });
});
