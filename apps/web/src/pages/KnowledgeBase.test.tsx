import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { KnowledgeBase } from "./KnowledgeBase";

describe("KnowledgeBase", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("filters incidents by search text", async () => {
    render(
      <MemoryRouter>
        <KnowledgeBase />
      </MemoryRouter>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    const initialCount = screen.getByText(/\d+ results/i).textContent;

    fireEvent.change(screen.getByPlaceholderText(/title, notes, ocr text, analysis text/i), {
      target: { value: "zzzznonexistent" },
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(screen.getByText(/0 results/i)).toBeInTheDocument();
    expect(initialCount).not.toMatch(/^0 /);
  });
});
