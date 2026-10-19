// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { tick } from "svelte";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ForecastEvent, Overview } from "$lib/types";
import ForecastChart from "./ForecastChart.svelte";

beforeAll(() => {
  Object.assign(SVGElement.prototype, {
    getBBox: () => ({ x: 0, y: 0, width: 0, height: 0 }),
    getBoundingClientRect: () => ({ left: 0, top: 0, right: 320, bottom: 280, width: 320, height: 280, x: 0, y: 0 }),
  });
});

const DATES = ["2026-10-16", "2026-10-17", "2026-10-19"];
const statement: ForecastEvent = { date: "2026-10-19", name: "Venture X (Sam) statement", amount: -2684.92, kind: "card", estimated: true, balance_after: 1200 };
const fc = (extra: Partial<Overview> = {}): Overview => ({
  today: DATES[0], dates: DATES, total: [4000, 4000, 1300], low: { date: DATES[2], balance: 1300 },
  accounts: [{ id: "chk", name: "Checking", kind: "checking", balance: 4000 }],
  events: [statement], cards: [], warnings: [], warning_links: [], ...extra,
});
const hover = (container: HTMLElement, clientX: number) => fireEvent.mouseMove(container.querySelector("rect.cursor-crosshair")!, { clientX });
const finger = (container: HTMLElement, type: "touchstart" | "touchmove", clientX: number) => {
  const e = Object.assign(new Event(type, { cancelable: true }), { touches: [{ clientX, clientY: 100 }] });
  return fireEvent(container.querySelector("rect.cursor-crosshair")!, e);
};
const tip = (container: HTMLElement) => container.querySelector<HTMLElement>(".pointer-events-none")!;

describe("ForecastChart's readout", () => {
  it("shows the day's budgeted spending as one line after its events", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) } });
    await hover(container, 312);
    expect(screen.getByText("Mon, Oct 19")).toBeInTheDocument();
    const rows = [...container.querySelectorAll(".pointer-events-none > div.flex")].map((r) => r.textContent);
    expect(rows).toEqual(["Venture X (Sam) statement (est.)−$2,684.92", "Budgeted spending (est.)−$15.08"]);
  });

  it("has no budgeted line on a day without any", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) } });
    await hover(container, 182);
    expect(screen.getByText("Sat, Oct 17")).toBeInTheDocument();
    expect(screen.queryByText("Budgeted spending (est.)")).not.toBeInTheDocument();
  });

  it("keeps each amount whole on one line, and lets a long name wrap", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) } });
    await hover(container, 312);
    for (const label of ["Venture X (Sam) statement (est.)", "Budgeted spending (est.)"]) {
      const name = screen.getByText(label);
      expect(name).toHaveClass("min-w-0");
      expect(name.nextElementSibling).toHaveClass("whitespace-nowrap", "shrink-0");
    }
  });
});

describe("ForecastChart's readout on a phone", () => {
  afterEach(() => vi.useRealTimers());

  it("stays in the top left of the chart while a finger drags, so two days can be compared", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc() } });
    await finger(container, "touchstart", 182);
    await finger(container, "touchmove", 182);
    expect(screen.getByText("Sat, Oct 17")).toBeInTheDocument();
    expect(tip(container).style.left).toBe("52px");
    expect(tip(container).style.top).toBe("0px");
    await finger(container, "touchmove", 312);
    expect(screen.getByText("Mon, Oct 19")).toBeInTheDocument();
    expect(tip(container).style.left).toBe("52px");
    expect(tip(container).style.top).toBe("0px");
  });

  it("leaves a drag up or down to scroll the page, and a sideways one still scrubs", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc() } });
    const drag = (clientX: number, clientY: number) => {
      const e = Object.assign(new Event("touchmove", { cancelable: true }), { touches: [{ clientX, clientY }] });
      fireEvent(container.querySelector("rect.cursor-crosshair")!, e);
      return e;
    };
    await finger(container, "touchstart", 182);
    expect(drag(184, 160).defaultPrevented).toBe(false);
    expect(tip(container)).toBeNull();
    await finger(container, "touchstart", 182);
    expect(drag(260, 100).defaultPrevented).toBe(true);
    await tick();
    expect(screen.getByText("Mon, Oct 19")).toBeInTheDocument();
  });

  it("follows the mouse again once the pointer is a mouse, but not for the mouse move a tap sends", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { container } = render(ForecastChart, { props: { fc: fc() } });
    await finger(container, "touchstart", 182);
    await finger(container, "touchmove", 182);
    expect(tip(container).style.top).toBe("0px");
    await hover(container, 312);
    expect(tip(container).style.left).toBe("52px");
    vi.advanceTimersByTime(2000);
    await hover(container, 312);
    expect(tip(container).style.left).not.toBe("52px");
    expect(tip(container).style.top).not.toBe("0px");
  });
});

describe("ForecastChart from the keyboard", () => {
  const slider = () => screen.getByRole("slider");
  const press = (key: string, extra: KeyboardEventInit = {}) => fireEvent.keyDown(slider(), { key, ...extra });
  const readout = (container: HTMLElement) => container.querySelector<HTMLElement>("[data-testid=forecast-readout]");
  const rows = (container: HTMLElement) => [...container.querySelectorAll("[data-testid=forecast-readout] > div.flex")].map((r) => r.textContent);

  it("is one focus stop, a slider over the days that starts on today", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc() } });
    expect(container.querySelectorAll("[tabindex='0']")).toHaveLength(1);
    expect(slider()).toHaveAttribute("aria-valuemin", "0");
    expect(slider()).toHaveAttribute("aria-valuemax", "2");
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
    expect(slider()).toHaveAttribute("aria-label", "Projected balance over the next 2 days, $4,000.00 today to $1,300.00 on Monday, October 19, 2026");
    expect(readout(container)).toBeNull();
    slider().focus();
    await tick();
    expect(readout(container)).toHaveTextContent("Fri, Oct 16");
    expect(readout(container)).toHaveTextContent("$4,000.00");
  });

  it("steps a day with the arrows and goes to the ends with Home and End, never past them", async () => {
    render(ForecastChart, { props: { fc: fc() } });
    await press("ArrowRight");
    expect(slider()).toHaveAttribute("aria-valuenow", "1");
    await press("ArrowRight");
    await press("ArrowRight");
    expect(slider()).toHaveAttribute("aria-valuenow", "2");
    await press("ArrowLeft");
    expect(slider()).toHaveAttribute("aria-valuenow", "1");
    await press("Home");
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
    await press("ArrowLeft");
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
    await press("End");
    expect(slider()).toHaveAttribute("aria-valuenow", "2");
  });

  it("announces the day's date, balance and events, and says so when a day has none", async () => {
    render(ForecastChart, { props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) } });
    await press("ArrowRight");
    expect(slider().getAttribute("aria-valuetext")).toBe("Saturday, October 17, 2026: balance $4,000.00. Nothing scheduled.");
    await press("End");
    expect(slider().getAttribute("aria-valuetext")).toBe(
      "Monday, October 19, 2026: balance $1,300.00. Venture X (Sam) statement (estimated) −$2,684.92; Budgeted spending (estimated) −$15.08.");
  });

  it("shows the same readout for a day as the pointer does", async () => {
    const props = { fc: fc({ spend: { "2026-10-19": 15.08 } }) };
    const pointer = render(ForecastChart, { props });
    await hover(pointer.container, 312);
    const byPointer = rows(pointer.container);
    pointer.unmount();
    const { container } = render(ForecastChart, { props });
    await press("End");
    expect(rows(container)).toEqual(byPointer);
    expect(rows(container)).toEqual(["Venture X (Sam) statement (est.)−$2,684.92", "Budgeted spending (est.)−$15.08"]);
  });

  it("reads a negative balance as negative", async () => {
    render(ForecastChart, { props: { fc: fc({ total: [400, -250.5, 100], low: { date: DATES[1], balance: -250.5 } }) } });
    await press("ArrowRight");
    expect(slider().getAttribute("aria-valuetext")).toContain("balance -$250.50");
  });

  it("jumps a tenth of the chart with Shift, and Escape puts the readout away", async () => {
    const dates = Array.from({ length: 31 }, (_, i) => `2026-10-${String(i + 1).padStart(2, "0")}`);
    const { container } = render(ForecastChart, { props: { fc: fc({ dates, total: dates.map((_, i) => 1000 + i), events: [], low: { date: dates[0], balance: 1000 } }) } });
    await press("ArrowRight", { shiftKey: true });
    expect(slider()).toHaveAttribute("aria-valuenow", "3");
    await press("Escape");
    expect(readout(container)).toBeNull();
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
  });

  it("puts the readout on the forecast's lowest day with Lowest balance, and the focus on the chart", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc({ total: [4000, -120, -120], low: { date: DATES[1], balance: -120 } }) } });
    await fireEvent.click(screen.getByRole("button", { name: "Lowest balance" }));
    expect(slider()).toHaveAttribute("aria-valuenow", "1");
    expect(slider()).toHaveFocus();
    expect(readout(container)).toHaveTextContent("Sat, Oct 17");
    expect(readout(container)).toHaveTextContent("-$120.00");
    await press("ArrowRight");
    expect(slider()).toHaveAttribute("aria-valuenow", "2");
  });

  it("finds the lowest day itself when the forecast's low isn't on the chart", async () => {
    render(ForecastChart, { props: { fc: fc({ total: [4000, 900, 1300], low: { date: "2027-01-01", balance: 0 } }) } });
    await fireEvent.click(screen.getByRole("button", { name: "Lowest balance" }));
    expect(slider()).toHaveAttribute("aria-valuenow", "1");
  });

  it("puts the readout away when the focus leaves", async () => {
    const { container } = render(ForecastChart, { props: { fc: fc() } });
    await press("End");
    expect(readout(container)).not.toBeNull();
    await fireEvent.blur(slider());
    expect(readout(container)).toBeNull();
  });

  it("drops a day a shorter forecast no longer has, and keeps one it still has", async () => {
    const { container, rerender } = render(ForecastChart, { props: { fc: fc() } });
    await press("End");
    await rerender({ fc: fc({ dates: DATES.slice(0, 2), total: [4000, 4000], events: [], low: { date: DATES[0], balance: 4000 } }) });
    expect(readout(container)).toBeNull();
    expect(slider()).toHaveAttribute("aria-valuemax", "1");
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
    await press("End");
    await rerender({ fc: fc() });
    expect(slider()).toHaveAttribute("aria-valuenow", "1");
    expect(readout(container)).toHaveTextContent("Sat, Oct 17");
  });

  it("has a valid slider for a single day, and none for an empty forecast", async () => {
    const one = render(ForecastChart, { props: { fc: fc({ dates: [DATES[0]], total: [4000], events: [], low: { date: DATES[0], balance: 4000 } }) } });
    expect(slider()).toHaveAttribute("aria-valuemax", "0");
    expect(slider()).toHaveAttribute("aria-label", "Projected balance for today, $4,000.00");
    await press("ArrowRight");
    await press("End");
    expect(slider()).toHaveAttribute("aria-valuenow", "0");
    expect(slider().getAttribute("aria-valuetext")).toBe("Friday, October 16, 2026: balance $4,000.00. Nothing scheduled.");
    one.unmount();
    render(ForecastChart, { props: { fc: fc({ dates: [], total: [], events: [] }) } });
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByRole("button", { name: "Lowest balance" })).toBeNull();
    expect(screen.getByText("No cash accounts in the forecast yet.")).toBeInTheDocument();
  });

  it("shows a visible focus ring for the keyboard only", () => {
    render(ForecastChart, { props: { fc: fc() } });
    expect(slider()).toHaveClass("focus-visible:ring-2", "outline-none");
  });
});

describe("ForecastChart, compact", () => {
  it("marks the lowest day and writes the last day's balance at the line's end", () => {
    const { container } = render(ForecastChart, { props: { fc: fc({ total: [4000, 1300, 2500] }), compact: true } });
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", expect.stringMatching(/ 120$/));
    expect(container.querySelectorAll("[data-low]")).toHaveLength(1);
    expect(container.querySelector("[data-end]")).toHaveTextContent("$2,500");
  });

  it("keeps the value axis on the compact chart", () => {
    const { container } = render(ForecastChart, { props: { fc: fc({ total: [4000, 1300, 2500] }), compact: true } });
    expect(container.querySelectorAll("line[stroke='var(--border)']").length).toBeGreaterThan(0);
  });

  it("leaves the marks off the full chart", () => {
    const { container } = render(ForecastChart, { props: { fc: fc() } });
    expect(container.querySelector("[data-low]")).toBeNull();
    expect(container.querySelector("[data-end]")).toBeNull();
  });
});
