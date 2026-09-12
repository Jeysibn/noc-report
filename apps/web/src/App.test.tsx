import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import App from "./App";

describe("App", () => {
  it("boots and renders the dashboard inside the app shell", async () => {
    render(
      <BrowserRouter>
        <App />
      </BrowserRouter>,
    );
    expect(screen.getByRole("heading", { name: /dashboard/i })).toBeInTheDocument();
    expect(screen.getByText(/noc report builder/i)).toBeInTheDocument();
    expect(await screen.findByText(/night shift/i)).toBeInTheDocument();
    expect(await screen.findByText("INC-1042")).toBeInTheDocument();
  });
});
