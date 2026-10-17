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

  it("keeps its size by default and lets a page grow its box and month label", () => {
    const { container, unmount } = render(MonthPicker, { month: "2026-01", onchange: vi.fn() });
    expect(screen.getByText("January 2026")).toHaveClass("min-w-36");
    expect(container.firstElementChild!.className).not.toMatch(/flex-1/);
    expect(screen.getByText("January 2026").className).not.toMatch(/flex-1/);
    unmount();
    const grown = render(MonthPicker, { month: "2026-01", onchange: vi.fn(), class: "phone:flex-1", labelClass: "phone:min-w-0 phone:flex-1" });
    expect(grown.container.firstElementChild).toHaveClass("phone:flex-1", "inline-flex");
    expect(screen.getByText("January 2026")).toHaveClass("min-w-36", "phone:min-w-0", "phone:flex-1");
  });

  it("steps across year ends", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -13)).toBe("2024-12");
  });
});
