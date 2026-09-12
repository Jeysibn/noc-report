import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { __setCurrentUserForTests } from "@/lib/session";

function signInAs(role: "NOC" | "Admin") {
  __setCurrentUserForTests({
    id: "test-user",
    username: "operator",
    displayName: "Operator",
    email: null,
    roles: [role],
    permissions: [],
  });
}

describe("Sidebar", () => {
  it("hides Admin and Analytics nav items for the NOC role", () => {
    signInAs("NOC");
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: /admin/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /analytics/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /dashboard/i })).toBeInTheDocument();
  });

  it("shows Admin and Analytics nav items for the Admin role", () => {
    signInAs("Admin");
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: /admin/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /analytics/i })).toBeInTheDocument();
  });
});
