// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import Segmented from "./toggle-group.svelte";

describe("Segmented", () => {
  it("scrolls when it doesn't fit, centred only while there's room (safe), so the first option is never clipped", () => {
    render(Segmented, { value: "a", label: "Period", options: [{ value: "a", label: "This month" }, { value: "b", label: "Last year" }] });
    const group = screen.getByRole("group", { name: "Period" });
    expect(group).toHaveClass("max-w-full", "overflow-x-auto", "justify-center-safe");
    expect(group).not.toHaveClass("justify-center");
  });
});
