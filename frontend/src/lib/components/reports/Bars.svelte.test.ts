// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import { monthLabel } from "$lib/format";
import Bars from "./Bars.svelte";

const MONTHS = ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"];
const props = (labels: string[]) => ({
  labels, series: [{ name: "Groceries", color: "red", values: labels.map((_, i) => 100 + i) }], label: "Spending by month",
  tipTitle: (i: number) => monthLabel(labels.map((_, k) => `2026-${String(k + 1).padStart(2, "0")}`)[i]),
});
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

  it("keeps columns and labels together when a readout is showing and the months change", async () => {
    const { rerender } = render(Bars, props(MONTHS));
    const svg = screen.getByRole("img", { name: "Spending by month" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    await fireEvent.mouseMove(svg.querySelector("rect[data-overlay]")!, { clientX: 310 });
    expect(screen.getByText("December 2026")).toBeInTheDocument();

    const six = MONTHS.slice(6);
    await rerender(props(six));
    const cols = columns(svg);
    expect(cols).toHaveLength(6);
    for (const [text, x] of labelled(svg)) expect(cols[six.indexOf(text!)]).toBeCloseTo(x);
    expect(labelled(svg).map(([t]) => t)).toContain("Oct");
    expect(document.querySelector(".bg-popover")).toBeNull();
  });

  it("draws a month under way faint and labels it so far", () => {
    render(Bars, { ...props(["Aug", "Sep", "Oct"]), partial: true });
    const svg = screen.getByRole("img", { name: "Spending by month" });
    expect(svg.querySelectorAll("path[data-partial]")).toHaveLength(1);
    expect(svg.querySelectorAll("path:not([data-partial])")).toHaveLength(2);
    expect(labelled(svg).map(([t]) => t)).toEqual(["Aug", "Sep", "Oct (so far)"]);
  });

  it("leaves out the last label when it would run into the one before", () => {
    const thirteen = [...MONTHS, "Nov"];
    render(Bars, { ...props(thirteen), partial: true });
    const svg = screen.getByRole("img", { name: "Spending by month" });
    const xs = labelled(svg).map(([, x]) => x);
    for (let i = 1; i < xs.length; i++) expect(xs[i] - xs[i - 1]).toBeGreaterThan(30);
    expect(labelled(svg).map(([t]) => t)).not.toContain("Nov (so far)");
  });

  it("draws the average as a reference line", () => {
    render(Bars, { ...props(MONTHS), avg: 105 });
    expect(document.querySelector("line[data-average]")).not.toBeNull();
  });

  it("steps through the months with the arrow keys and opens one with Enter", async () => {
    const onpick = vi.fn();
    render(Bars, { ...props(MONTHS), onpick });
    const chart = screen.getByRole("slider", { name: "Spending by month" });
    await fireEvent.focus(chart);
    expect(screen.getByText("December 2026")).toBeInTheDocument();
    await fireEvent.keyDown(chart, { key: "ArrowLeft" });
    await fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(screen.getByText("October 2026")).toBeInTheDocument();
    expect(chart).toHaveAttribute("aria-valuenow", "9");
    await fireEvent.keyDown(chart, { key: "Home" });
    await fireEvent.keyDown(chart, { key: "Enter" });
    expect(onpick).toHaveBeenCalledWith(0, null);
    await fireEvent.blur(chart);
    expect(document.querySelector(".bg-popover")).toBeNull();
  });

  it("opens the segment clicked, and on a touch screen only on a second tap", async () => {
    const onpick = vi.fn();
    const series = [{ name: "Rent", color: "blue", values: [100, 100] }, { name: "Food", color: "green", values: [100, 100] }];
    render(Bars, { labels: ["Sep", "Oct"], series, label: "Spending", tipTitle: (i: number) => ["Sep", "Oct"][i], onpick });
    const svg = screen.getByRole("img", { name: "Spending" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    const overlay = svg.querySelector("rect[data-overlay]")!;
    await fireEvent.pointerDown(overlay, { pointerType: "mouse" });
    await fireEvent.click(overlay, { clientX: 260, clientY: 40 });
    expect(onpick).toHaveBeenLastCalledWith(1, "Food");
    await fireEvent.click(overlay, { clientX: 260, clientY: 220 });
    expect(onpick).toHaveBeenLastCalledWith(1, "Rent");
    onpick.mockClear();
    await fireEvent.mouseLeave(overlay);
    await fireEvent.pointerDown(overlay, { pointerType: "touch" });
    await fireEvent.click(overlay, { clientX: 100, clientY: 220 });
    expect(onpick).not.toHaveBeenCalled();
    expect(document.querySelector(".bg-popover")).toHaveTextContent("Sep");
    await fireEvent.pointerDown(overlay, { pointerType: "touch" });
    await fireEvent.click(overlay, { clientX: 100, clientY: 220 });
    expect(onpick).toHaveBeenCalledWith(0, "Rent");
  });

  it("puts the readout away on a tap outside the chart", async () => {
    render(Bars, props(MONTHS));
    const svg = screen.getByRole("img", { name: "Spending by month" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    await fireEvent.mouseMove(svg.querySelector("rect[data-overlay]")!, { clientX: 310 });
    expect(document.querySelector(".bg-popover")).not.toBeNull();
    await fireEvent.pointerDown(document.body);
    expect(document.querySelector(".bg-popover")).toBeNull();
  });

  it("shows amounts in whole dollars", async () => {
    render(Bars, { labels: ["Sep"], series: [{ name: "Rent", color: "blue", values: [1234.56] }], label: "Spending", tipTitle: () => "September" });
    const svg = screen.getByRole("img", { name: "Spending" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    await fireEvent.mouseMove(svg.querySelector("rect[data-overlay]")!, { clientX: 200 });
    expect(screen.getByText("$1,235")).toBeInTheDocument();
  });
});
