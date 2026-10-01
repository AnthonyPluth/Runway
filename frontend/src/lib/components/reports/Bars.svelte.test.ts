// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import { monthLabel } from "$lib/format";
import Bars from "./Bars.svelte";

const MONTHS = ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"];
// As the Over time report draws it: the readout's title is the month in full, from the report's months.
const props = (labels: string[]) => ({
  labels, series: [{ name: "Groceries", color: "red", values: labels.map((_, i) => 100 + i) }], label: "Spending by month",
  tipTitle: (i: number) => monthLabel(labels.map((_, k) => `2026-${String(k + 1).padStart(2, "0")}`)[i]),
});
// The middle of each column, from its path's left and right edges.
const columns = (svg: Element) => [...svg.querySelectorAll("path")].map((p) => {
  const xs = [...p.getAttribute("d")!.matchAll(/([MQ ])(-?[\d.]+),|H(-?[\d.]+)/g)].map((m) => Number(m[2] ?? m[3]));
  return (Math.min(...xs) + Math.max(...xs)) / 2;
});
const labelled = (svg: Element) => [...svg.querySelectorAll("text[text-anchor=middle]")].map((t) => [t.textContent, Number(t.getAttribute("x"))] as const);

describe("Bars", () => {
  it("puts each month's column under its label", () => {
    render(Bars, props(MONTHS));
    const svg = screen.getByRole("img", { name: "Spending by month" }), cols = columns(svg);
    expect(cols).toHaveLength(12);
    for (const [text, x] of labelled(svg)) expect(cols[MONTHS.indexOf(text!)]).toBeCloseTo(x);
  });

  // On a phone the readout stays after the finger lifts. Going from 12 months to 6 with it on the last month used to
  // throw while redrawing, which left the new labels over the old columns (bunched into the right half).
  it("keeps columns and labels together when a readout is showing and the months change", async () => {
    const { rerender } = render(Bars, props(MONTHS));
    const svg = screen.getByRole("img", { name: "Spending by month" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    await fireEvent.mouseMove(svg.querySelector("rect.cursor-pointer")!, { clientX: 310 });
    expect(screen.getByText("December 2026")).toBeInTheDocument();   // the readout, on the last month

    const six = MONTHS.slice(6);
    await rerender(props(six));
    const cols = columns(svg);
    expect(cols).toHaveLength(6);
    for (const [text, x] of labelled(svg)) expect(cols[six.indexOf(text!)]).toBeCloseTo(x);
    expect(labelled(svg).map(([t]) => t)).toContain("Oct");
    expect(document.querySelector(".bg-popover")).toBeNull();   // no readout for a month that's gone
  });
});
