// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import MonthPicker, { shiftMonth } from "./MonthPicker.svelte";

describe("MonthPicker", () => {
  it("shows the month and asks for the one before or after", async () => {
    const onchange = vi.fn();
    render(MonthPicker, { month: "2026-01", onchange });
    expect(screen.getByText("January 2026")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(onchange).toHaveBeenLastCalledWith("2025-12");
    await userEvent.click(screen.getByRole("button", { name: "Next month" }));
    expect(onchange).toHaveBeenLastCalledWith("2026-02");
  });

  it("steps across year ends", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -13)).toBe("2024-12");
  });
});
