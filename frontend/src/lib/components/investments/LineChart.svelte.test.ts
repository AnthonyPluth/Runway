// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import LineChart from "./LineChart.svelte";
import type { Series } from "./types";

const days = (n: number) => Array.from({ length: n }, (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`);
const props = (xs: string[]) => ({ xs, series: [{ name: "Net worth", cls: "s-main", values: xs.map((_, i) => 1000 + i) }] as Series[] });

describe("LineChart", () => {
  // On a phone the readout stays after the finger lifts; a shorter range (a year to a month) used to leave it past
  // the last day, which threw while redrawing and left the chart half updated.
  it("drops the readout when the day it was on is no longer in the chart", async () => {
    const { rerender } = render(LineChart, props(days(30)));
    const svg = screen.getByRole("img", { name: "Net worth" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 240 }) as DOMRect;
    await fireEvent.mouseMove(svg.querySelector("rect[role=presentation]")!, { clientX: 210 });
    expect(screen.getByText("Wed, Sep 30")).toBeInTheDocument();

    await rerender(props(days(7)));
    expect(document.querySelector(".bg-popover")).toBeNull();
  });
});
