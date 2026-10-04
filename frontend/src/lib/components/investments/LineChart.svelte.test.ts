// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { tick } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import LineChart from "./LineChart.svelte";

let reportWidth = 0;
globalThis.ResizeObserver = class {
  constructor(private cb: ResizeObserverCallback) {}
  observe(el: Element) {
    if (!reportWidth) return;
    Object.defineProperty(el, "clientWidth", {
      configurable: true,
      value: reportWidth,
    });
    queueMicrotask(() =>
      this.cb(
        [{ target: el } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      ),
    );
  }
  unobserve() {}
  disconnect() {}
};
import type { Series } from "./types";

const days = (n: number) =>
  Array.from(
    { length: n },
    (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`,
  );
const props = (xs: string[]) => ({
  xs,
  series: [
    { name: "Net worth", cls: "s-main", values: xs.map((_, i) => 1000 + i) },
  ] as Series[],
});

describe("LineChart", () => {
  it("drops the readout when the day it was on is no longer in the chart", async () => {
    const { rerender } = render(LineChart, props(days(30)));
    const svg = screen.getByRole("slider", { name: /^Net worth/ });
    svg.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 320, height: 240 }) as DOMRect;
    await fireEvent.mouseMove(svg.querySelector("rect[role=presentation]")!, {
      clientX: 210,
    });
    expect(screen.getByText("Wed, Sep 30")).toBeInTheDocument();

    await rerender(props(days(7)));
    expect(document.querySelector(".bg-popover")).toBeNull();
  });

  it('offers the numbers as a table, or keeps it for screen readers only with table="sr"', async () => {
    const { unmount } = render(LineChart, props(days(5)));
    expect(screen.getByText("Show as table")).toBeInTheDocument();
    unmount();
    render(LineChart, { ...props(days(5)), table: "sr" });
    expect(screen.queryByText("Show as table")).not.toBeInTheDocument();
    expect(screen.getByRole("table").closest(".sr-only")).not.toBeNull();
  });

  it("draws a labelled dashed line at the mark, and nothing when it's outside the chart", async () => {
    const { rerender } = render(LineChart, {
      ...props(days(11)),
      mark: { at: 5, label: "Today" },
    });
    const line = screen.getByTestId("mark-line");
    expect(Number(line.getAttribute("x1"))).toBeCloseTo(
      56 + (320 - 56 - 110) / 2,
      1,
    );
    expect(line.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByTestId("mark-shade")).toBeInTheDocument();

    await rerender({ ...props(days(11)), mark: { at: 11, label: "Today" } });
    expect(screen.queryByTestId("mark-line")).toBeNull();
    expect(screen.queryByText("Today")).toBeNull();
  });

  it("sums up the chart for screen readers: where each line starts and ends, and the change", () => {
    render(LineChart, {
      xs: days(3),
      series: [
        { name: "Value", cls: "s-main", values: [1000, 900, 1250] },
        { name: "Net invested", cls: "s-muted", values: [1000, 1000, 800] },
      ],
      fmtTip: (v: number) => `$${v}`,
    });
    expect(
      screen
        .getByRole("slider")
        .getAttribute("aria-label")!
        .replace(/\u00a0/g, " "),
    ).toBe(
      "Value: $1000 on Sep 1, 2026 to $1250 on Sep 3, 2026, up $250; Net invested: $1000 on Sep 1, 2026 to $800 on Sep 3, 2026, down $200",
    );
  });

  it("steps the readout with the arrow keys once it has focus, and tells the page which day it's on", async () => {
    const onpoint = vi.fn();
    render(LineChart, { ...props(days(5)), onpoint });
    const chart = screen.getByRole("slider");
    chart.focus();
    await fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(chart).toHaveAttribute("aria-valuenow", "3");
    expect(chart.getAttribute("aria-valuetext")).toMatch(
      /^Sep\s4,\s2026: Net worth 1003$/,
    );
    expect(onpoint).toHaveBeenLastCalledWith(3);
    await fireEvent.keyDown(chart, { key: "Home" });
    expect(chart).toHaveAttribute("aria-valuenow", "0");
    await fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(chart).toHaveAttribute("aria-valuenow", "0");
    await fireEvent.keyDown(chart, { key: "End" });
    expect(chart).toHaveAttribute("aria-valuenow", "4");
    await fireEvent.keyDown(chart, { key: "Escape" });
    expect(onpoint).toHaveBeenLastCalledWith(null);
  });

  it("puts the readout away on a tap outside the chart", async () => {
    render(LineChart, props(days(5)));
    const chart = screen.getByRole("slider");
    await fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(document.querySelector(".bg-popover")).not.toBeNull();
    await fireEvent.pointerDown(chart);
    expect(document.querySelector(".bg-popover")).not.toBeNull();
    await fireEvent.pointerDown(document.body);
    expect(document.querySelector(".bg-popover")).toBeNull();
  });

  describe("on a narrow screen", () => {
    afterEach(() => {
      reportWidth = 0;
    });
    const two = {
      xs: days(5),
      series: [
        { name: "Your portfolio", cls: "s-main", values: [0, 1, 2, 3, 4] },
        {
          name: "S&P 500 with a very long name indeed",
          cls: "s-alt",
          values: [0, 2, 1, 3, 2],
        },
      ] as Series[],
    };

    it("moves the names to a key under the chart and gives the lines the right margin", async () => {
      reportWidth = 390;
      const { container } = render(LineChart, two);
      await tick();
      const legend = screen.getByTestId("chart-legend");
      expect(legend).toHaveTextContent("Your portfolio");
      expect(
        legend.querySelector("[title='S&P 500 with a very long name indeed']"),
      ).toHaveClass("truncate");
      expect(container.querySelector("svg")!.textContent).not.toContain(
        "Your portfolio",
      );
      expect(
        Number(container.querySelector("svg line")!.getAttribute("x2")),
      ).toBe(390 - 16);
    });

    it("keeps the names at the line ends when there's room, cut short when long", async () => {
      reportWidth = 800;
      const { container } = render(LineChart, two);
      await tick();
      expect(screen.queryByTestId("chart-legend")).toBeNull();
      const svg = container.querySelector("svg")!;
      expect(svg.textContent).toContain("Your portfolio");
      expect(svg.textContent).toContain("S&P 500 with a…");
      expect(svg.querySelector("title")!.textContent).toBe(
        "S&P 500 with a very long name indeed",
      );
    });
  });
});
