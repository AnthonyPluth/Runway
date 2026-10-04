// @vitest-environment jsdom
import { fireEvent, render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("$lib/filters.svelte", async (orig) => ({
  ...(await orig<typeof import("$lib/filters.svelte")>()),
  showTransactions: vi.fn(),
}));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { showTransactions } from "$lib/filters.svelte";
import type { Category } from "$lib/types";
import Income from "./Income.svelte";
import { reportState } from "./state.svelte";
import Trends from "./Trends.svelte";
import type { IncomeReport, SpendingReport } from "./types";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(showTransactions).mockReset();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-03T12:00:00"));
  Object.assign(reportState, { group: "category", focus: null, months: 12 });
  categories.list = [
    { name: "Groceries", color: "#008300", icon: "🛒" },
    { name: "Dining", color: "#d95926", icon: "🍽️" },
  ] as Category[];
});
afterEach(() => {
  vi.useRealTimers();
  categories.list = [];
});

describe("Income", () => {
  const report: IncomeReport = {
    months: [
      {
        month: "2026-07",
        income: 3000,
        spending: 1000,
        net: 2000,
        rate: 0.6667,
      },
      {
        month: "2026-08",
        income: 3000,
        spending: 3500,
        net: -500,
        rate: -0.1667,
      },
      { month: "2026-09", income: 0, spending: 2140, net: -2140, rate: null },
    ],
    year: {
      year: 2026,
      months: 3,
      income: 6000.4,
      spending: 6640.4,
      net: -640,
      rate: -0.1067,
    },
  };

  it("sums up this year in tiles that add up, counting the months with history", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    expect(await screen.findByText("This year")).toBeInTheDocument();
    const strip = screen.getByText("3 months so far").closest("dl")!;
    expect(within(strip).getByText("Money in")).toBeInTheDocument();
    expect(within(strip).getByText("Money out")).toBeInTheDocument();
    expect(strip).toHaveTextContent("$6,000");
    expect(strip).toHaveTextContent("$6,640");
    expect(within(strip).getByText("−$640")).toBeInTheDocument();
    expect(within(strip).getByText("Savings rate −11%")).toBeInTheDocument();
  });

  it("calls a shortfall in the year under way left over, without a glyph or red", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    const label = await screen.findByText("Left over", { selector: "dt span" });
    expect(label.closest("[data-tone]")).toBeNull();
    expect(screen.queryByText(/▲/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Spent more than came in/),
    ).not.toBeInTheDocument();
  });

  it("lists only the months it was sent, this month marked so far, in whole dollars", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    await screen.findByText("This year");
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(
      within(table).getByRole("columnheader", { name: "Money out" }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("row", { name: /September 2026.*\(so far\)/ }),
    ).toHaveTextContent("$2,140");
  });

  it("changes only the chart's months, from the chart's own control", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    await screen.findByText("This year");
    await userEvent.click(
      within(screen.getByRole("group", { name: "Months" })).getByText(
        "6 months",
      ),
    );
    expect(api).toHaveBeenLastCalledWith(
      "/api/reports/income?end=2026-09&months=6",
    );
  });

  it("opens a month's money in or out in Transactions", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    const svg = await screen.findByRole("img", {
      name: "Money in and money out by month",
    });
    svg.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    const overlay = svg.querySelector("rect[data-overlay]")!;
    await fireEvent.keyDown(overlay, { key: "End" });
    await fireEvent.keyDown(overlay, { key: "Enter" });
    expect(showTransactions).toHaveBeenLastCalledWith({
      scope: "budget",
      month: "2026-09",
      kind: "",
    });
    expect(svg.querySelectorAll("path[data-partial]").length).toBeGreaterThan(
      0,
    );
  });
});

describe("Over time", () => {
  const spending = (
    months: string[],
    totals: number[],
    extra: Partial<SpendingReport> = {},
  ): SpendingReport => ({
    months,
    group: "category",
    totals,
    series: [
      {
        name: "Groceries",
        values: totals,
        total: totals.reduce((a, b) => a + b, 0),
      },
    ],
    ...extra,
  });
  const seriesTable = () => screen.getAllByRole("table").at(-1)!;

  it("averages the full months only, leaving out the month under way", async () => {
    vi.mocked(api).mockResolvedValue(
      spending(["2026-07", "2026-08", "2026-09"], [300, 500, 40]) as never,
    );
    render(Trends);
    const avg = await screen.findByText("$400 a month on average");
    expect(avg).toHaveAttribute(
      "title",
      "Average of the 2 full months before this one",
    );
    expect(
      within(seriesTable()).getByRole("row", { name: /Groceries/ }),
    ).toHaveTextContent("$400");
    expect(document.querySelector("line[data-average]")).not.toBeNull();
  });

  it("with only this month of history, shows no average and nothing to compare against", async () => {
    vi.mocked(api).mockResolvedValue(spending(["2026-09"], [40]) as never);
    render(Trends);
    await screen.findByText("Groceries", { selector: "td span" });
    expect(screen.queryByText(/a month on average/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: /^vs / }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Monthly average" }),
    ).not.toBeInTheDocument();
  });

  it("compares the month under way with last month up to the same day, never with all of it", async () => {
    const d = spending(["2026-07", "2026-08", "2026-09"], [300, 500, 40], {
      through: "2026-09-03",
    });
    d.series[0].same_point = { prev: 50, year_ago: null };
    d.series.push({
      name: "Dining",
      values: [10, 0, 25],
      total: 35,
      same_point: { prev: 0, year_ago: null },
    });
    vi.mocked(api).mockResolvedValue(d as never);
    render(Trends);
    const table = await vi.waitFor(() => seriesTable());
    expect(
      within(table).getByRole("columnheader", { name: "Sep (so far)" }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("columnheader", { name: "vs Aug 1–3" }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("row", { name: /Groceries/ }),
    ).toHaveTextContent("−20%");
    expect(
      within(table).getByRole("row", { name: /Groceries/ }),
    ).not.toHaveTextContent("−92%");
    expect(
      within(table).getByRole("row", { name: /Dining/ }),
    ).toHaveTextContent("New");
  });

  it("colors categories as Budget does, whatever their rank, with gray only for everything else", async () => {
    const d = spending(["2026-08", "2026-09"], [100, 50]);
    d.series = [
      { name: "Dining", values: [80, 40], total: 120 },
      { name: "Groceries", values: [20, 10], total: 30 },
      {
        name: "Everything else (3)",
        values: [5, 5],
        total: 10,
        other: true,
        members: ["A", "B", "C"],
      },
    ];
    vi.mocked(api).mockResolvedValue(d as never);
    render(Trends);
    const table = await vi.waitFor(() => seriesTable());
    const swatch = (name: RegExp) =>
      within(table).getByRole("row", { name }).querySelector("i")!.style
        .background;
    expect(swatch(/Dining/)).toBe("rgb(217, 89, 38)");
    expect(swatch(/Groceries/)).toBe("rgb(0, 131, 0)");
    expect(swatch(/Everything else/)).toBe("var(--cat-other)");
  });

  it("colors merchants by rank", async () => {
    const d = spending(["2026-08", "2026-09"], [100, 50], {
      group: "merchant",
    });
    d.series = [{ name: "Corner Shop", values: [80, 40], total: 120 }];
    reportState.group = "merchant";
    vi.mocked(api).mockResolvedValue(d as never);
    render(Trends);
    const table = await vi.waitFor(() => seriesTable());
    expect(
      within(table)
        .getByRole("row", { name: /Corner Shop/ })
        .querySelector("i")!.style.background,
    ).toBe("var(--cat-1)");
  });

  it("lists what's in everything else when its row is opened", async () => {
    const d = spending(["2026-08", "2026-09"], [100, 50]);
    d.series.push({
      name: "Everything else (2)",
      values: [5, 5],
      total: 10,
      other: true,
      members: ["Pets", "Gifts"],
    });
    vi.mocked(api).mockResolvedValue(d as never);
    render(Trends);
    const open = await screen.findByRole("button", {
      name: /Everything else \(2\)/,
    });
    expect(open).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Pets · Gifts")).not.toBeInTheDocument();
    await userEvent.click(open);
    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Pets · Gifts")).toBeInTheDocument();
  });

  it("shows one row on its own from its focus button", async () => {
    vi.mocked(api).mockResolvedValue(
      spending(["2026-08", "2026-09"], [100, 50]) as never,
    );
    render(Trends);
    const only = await screen.findByRole("button", {
      name: "Groceries: show only this",
    });
    await userEvent.click(only);
    expect(only).toHaveAttribute("aria-pressed", "true");
    expect(reportState.focus).toBe("Groceries");
    expect(
      screen.getByRole("button", { name: "Show everything" }),
    ).toBeInTheDocument();
  });

  it("has the months as a table", async () => {
    vi.mocked(api).mockResolvedValue(
      spending(["2026-08", "2026-09"], [100.4, 50], {
        through: "2026-09-03",
      }) as never,
    );
    render(Trends);
    await userEvent.click(await screen.findByText("Show as table"));
    const table = screen.getByRole("table", { name: "Spending by month" });
    expect(
      within(table).getByRole("row", { name: /August 2026 \$100/ }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("row", {
        name: /September 2026 \(so far\) \$50/,
      }),
    ).toBeInTheDocument();
  });

  it("opens a column's category in Transactions", async () => {
    vi.mocked(api).mockResolvedValue(
      spending(["2026-08", "2026-09"], [100, 50]) as never,
    );
    render(Trends);
    const svg = await screen.findByRole("img", { name: "Spending by month" });
    svg.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 320, height: 260 }) as DOMRect;
    const overlay = svg.querySelector("rect[data-overlay]")!;
    await fireEvent.pointerDown(overlay, { pointerType: "mouse" });
    await fireEvent.click(overlay, { clientX: 100, clientY: 230 });
    expect(showTransactions).toHaveBeenCalledWith({
      scope: "budget",
      month: "2026-08",
      category: "Groceries",
    });
  });

  it("opens a merchant or an account by what Transactions filters on", async () => {
    reportState.group = "account";
    const d = spending(["2026-08", "2026-09"], [100, 50], { group: "account" });
    d.series = [
      { name: "Joint card", account: "acct-7", values: [100, 50], total: 150 },
    ];
    vi.mocked(api).mockResolvedValue(d as never);
    render(Trends);
    const svg = await screen.findByRole("img", { name: "Spending by month" });
    const overlay = svg.querySelector("rect[data-overlay]")!;
    await fireEvent.click(
      screen.getByRole("button", { name: "Joint card: show only this" }),
    );
    await fireEvent.keyDown(overlay, { key: "End" });
    await fireEvent.keyDown(overlay, { key: "Enter" });
    expect(showTransactions).toHaveBeenCalledWith({
      scope: "budget",
      month: "2026-09",
      account: "acct-7",
      kind: "out",
    });
  });
});
