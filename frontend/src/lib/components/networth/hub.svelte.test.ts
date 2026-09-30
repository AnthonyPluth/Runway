// @vitest-environment jsdom
// Net worth is a hub: Summary, Investments, Equity and Retirement are tabs of one page.
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {} }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const PLAN = {
  plan: { people: [{ name: "You", birth_year: 1986, retire_age: 65, savings: 12000 }], plan_to_age: 95, spending: 48000, return_before: 0.05,
    return_after: 0.04, volatility: 0.12, inflation: 0.025, income: [], events: [], assets: [] },
  is_default: true, current: 0, year: 2026, assets: [], computed: { annual_spending: 48000, yearly_savings: 12000, expected_return: 0.05 },
};

beforeEach(() => {
  vi.mocked(api).mockReset();
  const summary = {
    today: "2026-09-30", net: 5000, assets: 5500, liabilities: 500, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
    assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [],
    groups: [{ key: "cash", label: "Cash", side: "asset", total: 5500, items: [] }],
  };
  vi.mocked(api).mockImplementation((async (path: string) =>
    (path === "/api/plaid/status" ? { inv_accounts: 0 } : path === "/api/networth" ? summary
      : path.startsWith("/api/investments?") ? { plan: PLAN } : {})) as never);
});

describe("the Net worth tabs", () => {
  it("shows the tab bar with Summary current by default", async () => {
    render(NetWorth);
    await screen.findByText("What makes it up");
    const tabs = screen.getByRole("navigation", { name: "Net worth" });
    expect(screen.getByRole("heading", { name: "Net worth" })).toBeInTheDocument();
    expect(Array.from(tabs.querySelectorAll("a")).map((a) => [a.textContent!.trim(), a.getAttribute("href")])).toEqual([
      ["Summary", "#networth"], ["Investments", "#networth/investments"], ["Equity", "#networth/equity"], ["Retirement", "#networth/retirement"]]);
    expect(screen.getByRole("link", { name: "Summary" })).toHaveAttribute("aria-current", "page");
  });

  it("opens Investments on #networth/investments", async () => {
    render(NetWorth, { sub: "investments" });
    expect(screen.getByRole("link", { name: "Investments" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("link", { name: "Connect an investment account" })).toBeInTheDocument();
  });

  it("opens Equity on #networth/equity, without its own back link", () => {
    render(NetWorth, { sub: "equity" });
    expect(screen.getByRole("link", { name: "Equity" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: /← Net worth/ })).not.toBeInTheDocument();
  });

  it("opens Retirement on #networth/retirement, pointing at Investments when there's nothing invested yet", async () => {
    render(NetWorth, { sub: "retirement" });
    expect(screen.getByRole("link", { name: "Retirement" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Summary" })).not.toHaveAttribute("aria-current");
    expect(await screen.findByText("Retirement planner")).toBeInTheDocument();
    expect(screen.getByText("Nothing to plan from yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Investments" })).toHaveAttribute("href", "#networth/investments");
    expect(screen.queryByText("Chance your money lasts")).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/api/investments?period=1Y");
  });

  it("offers a retry when the plan can't be loaded", async () => {
    vi.mocked(api).mockRejectedValue(new Error("nope"));
    render(NetWorth, { sub: "retirement" });
    expect(await screen.findByText(/Something went wrong: nope/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("no longer shows the planner on Investments", async () => {
    vi.mocked(api).mockImplementation((async (path: string) =>
      path === "/api/plaid/status" ? { inv_accounts: 0, items: [] } : {}) as never);
    render(NetWorth, { sub: "investments" });
    await screen.findByTestId("getting-started");
    expect(screen.queryByText("Retirement planner")).toBeNull();
  });
});
