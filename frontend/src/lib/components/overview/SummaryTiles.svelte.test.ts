// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));

import { api } from "$lib/api";
import { thisMonth } from "$lib/format";
import SummaryTiles from "./SummaryTiles.svelte";

const month = thisMonth();
const cat = (name: string, budget: number, spent: number) => ({ name, parent: null, path: [name], depth: 0, top: name, has_children: false,
  budget, pay_with: null, usual_account: null, available: budget, spent, own_spent: spent, left: budget - spent });
const pace = { month, prev_month: "2026-02", this: [], last: [], spent: 640, last_same_point: 500, last_total: 1200 };
const budget = (day: number, cats = [cat("Dining", 400, 300), cat("Travel", 600, 340)]) =>
  ({ month, days_in_month: 30, day, categories: cats, income: 0, income_rows: [], uncategorized: 0, pay_accounts: [] });
const networth = (history = [{ date: `${month}-01`, net: 90000, assets: 0, liabilities: 0 }, { date: `${month}-10`, net: 91250, assets: 0, liabilities: 0 }]) =>
  ({ today: `${month}-10`, net: 91250, assets: 0, liabilities: 0, groups: [], excluded: [], history, first_snapshot: null,
    change: { "30d": 1250, "90d": null, "1y": null } });
const serve = (b: unknown, n: unknown) => vi.mocked(api).mockImplementation((async (path: string) => {
  if (path === "/api/month_pace") return pace;
  if (path.startsWith("/api/budget")) return b;
  if (path === "/api/networth") return n;
  throw new Error(path);
}) as never);

beforeEach(() => { vi.mocked(api).mockReset(); });
afterEach(cleanup);

describe("SummaryTiles", () => {
  it("links the spending tile to Budget and the net worth tile to Net worth", async () => {
    serve(budget(15), networth());
    render(SummaryTiles);
    expect(await screen.findByRole("link", { name: /^Spending this month/ })).toHaveAttribute("href", "#budget");
    expect(await screen.findByRole("link", { name: /^Net worth/ })).toHaveAttribute("href", "#networth");
  });

  it("says how far over or under pace spending is, from the budgets and how far through the month it is", async () => {
    // $640 of $1,000 budgeted, half way through: $140 ahead of an even pace
    serve(budget(15), networth());
    const { container } = render(SummaryTiles);
    expect(await screen.findByText("$140 over pace")).toBeInTheDocument();
    expect(container.querySelector("[data-pace-marker]")).toHaveStyle({ left: "50%" });
    cleanup();
    serve(budget(24), networth());
    render(SummaryTiles);
    expect(await screen.findByText("$160 under pace")).toBeInTheDocument();
  });

  it("compares with last month's pace when nothing is budgeted", async () => {
    serve(budget(15, []), networth());
    render(SummaryTiles);
    expect(await screen.findByText("$140 over Feb’s pace")).toBeInTheDocument();
  });

  it("shows net worth with its change this month", async () => {
    serve(budget(15), networth());
    const { container } = render(SummaryTiles);
    expect(await screen.findByText("$91,250")).toBeInTheDocument();
    expect(screen.getByText("+$1,250.00")).toBeInTheDocument();
    expect(screen.getByText("this month")).toBeInTheDocument();
    expect(container.querySelector("polyline")).not.toBeNull();
  });

  it("says so when a tile can't load, keeps the other, and tries again", async () => {
    let down = true;
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/networth") return networth();
      if (down) throw new Error("down");
      return path === "/api/month_pace" ? pace : budget(15);
    }) as never);
    render(SummaryTiles);
    expect(await screen.findByRole("link", { name: /^Net worth/ })).toBeInTheDocument();
    expect(await screen.findByText("Couldn’t load spending")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Spending/ })).not.toBeInTheDocument();
    down = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: /^Spending/ })).toBeInTheDocument();
  });

  it("shows no change for the month when there's no snapshot from before it", async () => {
    serve(budget(15), networth([{ date: `${month}-05`, net: 90500, assets: 0, liabilities: 0 }, { date: `${month}-10`, net: 91250, assets: 0, liabilities: 0 }]));
    render(SummaryTiles);
    expect(await screen.findByText("No change yet this month")).toBeInTheDocument();
    expect(screen.queryByText("this month")).not.toBeInTheDocument();
  });

  it("says where the change starts from when the snapshot is much older than the month", async () => {
    serve(budget(15), networth([{ date: "2000-01-01", net: 90000, assets: 0, liabilities: 0 }, { date: `${month}-10`, net: 91250, assets: 0, liabilities: 0 }]));
    render(SummaryTiles);
    expect(await screen.findByText("+$1,250.00")).toBeInTheDocument();
    expect(screen.getByText("since Jan 1, 2000")).toBeInTheDocument();
    expect(screen.queryByText("this month")).not.toBeInTheDocument();
  });
});
