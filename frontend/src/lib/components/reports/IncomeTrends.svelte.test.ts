// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import Income from "./Income.svelte";
import Trends from "./Trends.svelte";
import type { IncomeReport, SpendingReport } from "./types";

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("Income vs spending", () => {
  // The server sends only the months from the first transaction on: three here, though 12 were asked for.
  const report: IncomeReport = {
    months: [
      { month: "2026-07", income: 3000, spending: 1000, net: 2000, rate: 0.6667 },
      { month: "2026-08", income: 3000, spending: 3500, net: -500, rate: -0.1667 },
      { month: "2026-09", income: 0, spending: 2140, net: -2140, rate: null },
    ],
    year: { year: 2026, months: 3, income: 6000, spending: 6640, net: -640, rate: -0.1067 },
  };

  it("sums up the year in a stat strip, counting the months with history", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    const strip = (await screen.findByText("Money in, 2026")).closest("dl")!;
    expect(strip).toHaveTextContent("3 months so far");
    expect(strip).toHaveTextContent("$6,000");
    expect(strip).toHaveTextContent("$6,640");
  });

  it("calls a shortfall in the year under way so far, without flagging it red", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    const label = await screen.findByText("Spent more than came in so far");
    expect(label.closest("[data-tone]")).toBeNull();
    expect(screen.queryByText(/▲/)).not.toBeInTheDocument();
  });

  it("lists only the months it was sent", async () => {
    vi.mocked(api).mockResolvedValue(report as never);
    render(Income);
    await screen.findByText("Money in, 2026");
    expect(screen.getAllByRole("row")).toHaveLength(4);   // the header and three months
  });
});

describe("Over time", () => {
  const spending = (months: string[], totals: number[]): SpendingReport => ({
    months, group: "category", totals, series: [{ name: "Groceries", values: totals, total: totals.reduce((a, b) => a + b, 0) }],
  });

  it("averages the full months only, leaving out the month under way", async () => {
    vi.mocked(api).mockResolvedValue(spending(["2026-07", "2026-08", "2026-09"], [300, 500, 40]) as never);
    render(Trends);
    const avg = await screen.findByText("$400 a month on average");
    expect(avg).toHaveAttribute("title", "Average of the 2 full months before this one");
    expect(screen.getByRole("row", { name: /Groceries/ })).toHaveTextContent("$400.00");   // the row's monthly average too
  });

  it("with only this month of history, shows no average and nothing to compare against", async () => {
    vi.mocked(api).mockResolvedValue(spending(["2026-09"], [40]) as never);
    render(Trends);
    await screen.findByRole("row", { name: /Groceries/ });
    expect(screen.queryByText(/a month on average/)).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /^vs / })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Monthly average" })).not.toBeInTheDocument();
  });
});
