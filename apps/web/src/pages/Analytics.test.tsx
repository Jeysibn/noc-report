import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Analytics } from "./Analytics";
import { __setCurrentUserForTests } from "@/lib/session";

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
});
