import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Admin } from "./Admin";
import { __setCurrentUserForTests } from "@/lib/session";

describe("Admin", () => {
  it("shows the Users tab by default and switches to Audit", async () => {
    __setCurrentUserForTests({
      id: "test-user",
      username: "admin",
      displayName: "Admin User",
      email: null,
      roles: ["Admin"],
      permissions: [],
    });
    const user = userEvent.setup();
    render(<Admin />);
    await act(async () => {});
    expect(screen.getByText(/jomel concon/i)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /audit/i }));
    expect(screen.getByText(/generated shift report/i)).toBeInTheDocument();
  });

  it("lets an authorized administrator add a user", async () => {
    __setCurrentUserForTests({
      id: "test-user",
      username: "admin",
      displayName: "Admin User",
      email: null,
      roles: ["Admin"],
      permissions: ["user.manage"],
    });
    const user = userEvent.setup();
    render(<Admin />);
    await act(async () => {});

    await user.click(screen.getByRole("button", { name: /add user/i }));
    await user.type(screen.getByLabelText(/username/i), "new.operator");
    await user.type(screen.getByLabelText(/display name/i), "New Operator");
    await user.type(screen.getByLabelText(/^email$/i), "operator@example.com");
    await user.type(
      screen.getByLabelText(/temporary password/i),
      "temporary123",
    );
    await user.click(screen.getByRole("button", { name: /create user/i }));

    expect(await screen.findByText("New Operator")).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: /add user/i }),
    ).not.toBeInTheDocument();
  });
});
