import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Admin } from "./Admin";
import { __setCurrentUserForTests } from "@/lib/session";
import { adminService } from "@/services";

afterEach(() => vi.restoreAllMocks());

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

  it("shows aggregated runtime and disabled feature state", async () => {
    __setCurrentUserForTests({
      id: "test-user",
      username: "admin",
      displayName: "Admin User",
      email: null,
      roles: ["Admin"],
      permissions: ["system.read"],
    });
    const user = userEvent.setup();
    render(<Admin />);
    await act(async () => {});

    await user.click(screen.getByRole("tab", { name: /ai configuration/i }));
    expect(await screen.findByText(/Profile: noc-log-analysis/)).toBeInTheDocument();
    expect(screen.getAllByText("healthy").length).toBeGreaterThan(0);
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.queryByText(/no external ai runtime/i)).not.toBeInTheDocument();
  });

  it("does not fetch Runtime or Storage until their tabs are opened", async () => {
    const runtimeSpy = vi.spyOn(adminService, "getRuntimeStatus");
    const storageSpy = vi.spyOn(adminService, "listStorageStatus");
    const user = userEvent.setup();
    render(<Admin />);
    await act(async () => {});

    expect(runtimeSpy).not.toHaveBeenCalled();
    expect(storageSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: /storage/i }));
    await screen.findByText("noc-evidence");
    expect(storageSpy).toHaveBeenCalledTimes(1);
    expect(runtimeSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: /ai configuration/i }));
    await screen.findByText(/Profile: noc-log-analysis/);
    expect(runtimeSpy).toHaveBeenCalledTimes(1);
  });

  it("refreshes Runtime manually and preserves the last successful snapshot when refresh fails", async () => {
    const runtimeSpy = vi.spyOn(adminService, "getRuntimeStatus");
    const user = userEvent.setup();
    render(<Admin />);
    await user.click(screen.getByRole("tab", { name: /ai configuration/i }));
    await screen.findByText(/Profile: noc-log-analysis/);
    expect(screen.getByText(/Last successful refresh/)).toBeInTheDocument();

    runtimeSpy.mockRejectedValueOnce(new Error("runtime unavailable"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/stale data/i);
    expect(screen.getByText(/Profile: noc-log-analysis/)).toBeInTheDocument();
    expect(screen.getByText(/Last successful refresh/)).toBeInTheDocument();
  });

  it("allows Storage status to be refreshed manually without polling", async () => {
    const storageSpy = vi.spyOn(adminService, "listStorageStatus");
    const user = userEvent.setup();
    render(<Admin />);
    await user.click(screen.getByRole("tab", { name: /storage/i }));
    await screen.findByText("noc-evidence");
    expect(storageSpy).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(storageSpy).toHaveBeenCalledTimes(2));
  });

  it("pauses Runtime polling while the document is hidden and refreshes when visible", async () => {
    const runtimeSpy = vi.spyOn(adminService, "getRuntimeStatus");
    const user = userEvent.setup();
    render(<Admin />);
    await user.click(screen.getByRole("tab", { name: /ai configuration/i }));
    await screen.findByText(/Profile: noc-log-analysis/);
    expect(runtimeSpy).toHaveBeenCalledTimes(1);

    const visibilityDescriptor = Object.getOwnPropertyDescriptor(
      document,
      "visibilityState",
    );
    try {
      vi.useFakeTimers();
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: "hidden",
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000);
      });
      expect(runtimeSpy).toHaveBeenCalledTimes(1);

      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: "visible",
      });
      await act(async () => {
        fireEvent(document, new Event("visibilitychange"));
      });
      expect(runtimeSpy).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
      if (visibilityDescriptor) {
        Object.defineProperty(document, "visibilityState", visibilityDescriptor);
      }
    }
  });
});
