// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ForecastEvent, Overview } from "$lib/types";
import ForecastChart from "./ForecastChart.svelte";

beforeAll(() => {
  Object.assign(SVGElement.prototype, {
    getBBox: () => ({ x: 0, y: 0, width: 0, height: 0 }),
    getBoundingClientRect: () => ({
      left: 0,
      top: 0,
      right: 320,
      bottom: 280,
      width: 320,
      height: 280,
      x: 0,
      y: 0,
    }),
  });
});

const DATES = ["2026-10-16", "2026-10-17", "2026-10-19"];
const statement: ForecastEvent = {
  date: "2026-10-19",
  name: "Venture X (Sam) statement",
  amount: -2684.92,
  kind: "card",
  estimated: true,
  balance_after: 1200,
};
const fc = (extra: Partial<Overview> = {}): Overview => ({
  today: DATES[0],
  dates: DATES,
  total: [4000, 4000, 1300],
  low: { date: DATES[2], balance: 1300 },
  accounts: [{ id: "chk", name: "Checking", kind: "checking", balance: 4000 }],
  events: [statement],
  cards: [],
  warnings: [],
  warning_links: [],
  ...extra,
});
const hover = (container: HTMLElement, clientX: number) =>
  fireEvent.mouseMove(container.querySelector("rect.cursor-crosshair")!, {
    clientX,
  });
const finger = (
  container: HTMLElement,
  type: "touchstart" | "touchmove",
  clientX: number,
) => {
  const e = Object.assign(new Event(type, { cancelable: true }), {
    touches: [{ clientX, clientY: 100 }],
  });
  return fireEvent(container.querySelector("rect.cursor-crosshair")!, e);
};
const tip = (container: HTMLElement) =>
  container.querySelector<HTMLElement>(".pointer-events-none")!;

describe("ForecastChart's readout", () => {
  it("shows the day's budgeted spending as one line after its events", async () => {
    const { container } = render(ForecastChart, {
      props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) },
    });
    await hover(container, 312);
    expect(screen.getByText("Mon, Oct 19")).toBeInTheDocument();
    const rows = [
      ...container.querySelectorAll(".pointer-events-none > div.flex"),
    ].map((r) => r.textContent);
    expect(rows).toEqual([
      "Venture X (Sam) statement (est.)−$2,684.92",
      "Budgeted spending (est.)−$15.08",
    ]);
  });

  it("has no budgeted line on a day without any", async () => {
    const { container } = render(ForecastChart, {
      props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) },
    });
    await hover(container, 182);
    expect(screen.getByText("Sat, Oct 17")).toBeInTheDocument();
    expect(
      screen.queryByText("Budgeted spending (est.)"),
    ).not.toBeInTheDocument();
  });

  it("keeps each amount whole on one line, and lets a long name wrap", async () => {
    const { container } = render(ForecastChart, {
      props: { fc: fc({ spend: { "2026-10-19": 15.08 } }) },
    });
    await hover(container, 312);
    for (const label of [
      "Venture X (Sam) statement (est.)",
      "Budgeted spending (est.)",
    ]) {
      const name = screen.getByText(label);
      expect(name).toHaveClass("min-w-0");
      expect(name.nextElementSibling).toHaveClass(
        "whitespace-nowrap",
        "shrink-0",
      );
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
