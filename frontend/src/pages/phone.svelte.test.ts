// @vitest-environment jsdom
// The pages on a phone: what they keep, and the note where a computer's part would be. A computer sees them as before.
import { cleanup, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, catLook: () => ({ color: "#888" }), categoryGroups: () => [], catParentOf: () => null }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { benefit, calls, card, churning } from "$lib/components/churning/fixtures";
import { viewport } from "$lib/phone.svelte";
import Churning from "./Churning.svelte";
import NetWorth from "./NetWorth.svelte";
import Reports from "./Reports.svelte";
import Settings from "./Settings.svelte";

const NOTE = /Open Runway on a computer to/;
beforeEach(() => { vi.mocked(api).mockReset(); app.state = { connected: true, brands: {} } as never; });
// Unmount first: the page would otherwise redraw as a computer's when the flag flips back.
afterEach(() => { cleanup(); viewport.phone = false; });

describe("Settings", () => {
  const data = (async (path: string) => (path === "/api/accounts" || path === "/api/rules" ? [] : path === "/api/push" ? { public_key: "aGVsbG8", prefs: {}, devices: [], recent: [] } : {})) as never;

  it("is Notifications on a phone, with no banner: no tabs, no rules, categories or connections", async () => {
    viewport.phone = true;
    vi.mocked(api).mockImplementation(data);
    render(Settings, { sub: "rules" });
    expect(await screen.findByText("This device")).toBeInTheDocument();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByText("Add a rule")).not.toBeInTheDocument();
  });

  it("has every tab on a computer", async () => {
    vi.mocked(api).mockImplementation(data);
    render(Settings, { sub: "notifications" });
    const tabs = await screen.findByRole("navigation", { name: "Settings" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent!.trim().replace(/\d+$/, ""))).toEqual(
      ["Accounts", "Connections", "Categories", "Rules", "Notifications", "Advanced"]);
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });
});

describe("Reports", () => {
  beforeEach(() => { vi.mocked(api).mockResolvedValue({ month: "2026-03", income: [], spending: [], total_in: 0, total_out: 0, net: 0 } as never); });

  it("is the cash flow summary on a phone, whichever report the link names", async () => {
    viewport.phone = true;
    render(Reports, { sub: "merchants" });
    expect(await screen.findByText(/No transactions in/)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Reports" })).not.toBeInTheDocument();
    expect(screen.getByText(/Open Runway on a computer to see spending over time/)).toBeInTheDocument();
    expect(vi.mocked(api).mock.calls.every(([p]) => String(p).startsWith("/api/cashflow"))).toBe(true);
  });

  it("has all five reports on a computer", async () => {
    render(Reports);
    expect(await screen.findByText(/No transactions in/)).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Reports" })).getAllByRole("link")).toHaveLength(5);
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });
});

describe("Churning", () => {
  const data = (over = {}) => vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/churning/best") ? { cards: [] } : churning({ cards: [card({ benefits: [benefit()] })], ...over }))) as never);

  it("is upcoming and the benefits to mark used on a phone, with no cards, bank bonuses or plans to edit", async () => {
    viewport.phone = true;
    data();
    render(Churning, { sub: "bank" });
    expect(await screen.findByText("Worth a year")).toBeInTheDocument();   // the benefits board, whatever tab the link named
    expect(screen.getAllByText(NOTE)).toHaveLength(1);   // said once, here and not again in the benefits
    expect(screen.getByText(/manage cards, bank bonuses, plans and rewards/)).toBeInTheDocument();
    for (const name of ["Add a card", "Add a bank bonus", "Add a to-do"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Cards or bank bonuses" })).not.toBeInTheDocument();
    expect(calls("/api/churning/best")).toHaveLength(0);
  });

  it("has its cards and tabs on a computer", async () => {
    data();
    render(Churning);
    expect(await screen.findByRole("button", { name: "Add a card" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Cards or bank bonuses" })).toBeInTheDocument();
  });
});

describe("Net worth", () => {
  const summary = {
    today: "2026-09-30", net: 5000, assets: 5500, liabilities: 500, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
    assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [{ id: "x", name: "Old card", org: null, kind: "credit", balance: -50 }],
    groups: [{ key: "cash", label: "Cash", side: "asset", total: 5500, items: [{ type: "account", id: "a1", name: "Checking", value: 5500, org: "Bank" }] }],
  };
  beforeEach(() => { vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/networth" ? summary : {})) as never); });

  it("is the total and what it's made of, read-only, on a phone; Investments and Equity are for a computer", async () => {
    viewport.phone = true;
    render(NetWorth);
    expect(await screen.findByText("$5,000")).toBeInTheDocument();
    expect(screen.getByTestId("makes-up")).toBeInTheDocument();   // the bar under the hero, not in a card
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add to Cash|Checking/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Not counted/)).not.toBeInTheDocument();
    const tabs = screen.getByRole("navigation", { name: "Net worth" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent!.trim())).toEqual(["Summary", "Retirement"]);
  });

  it.each([["investments", "see your investments"], ["equity", "see and edit your equity"]])("says %s is for a computer", (sub, what) => {
    viewport.phone = true;
    render(NetWorth, { sub });
    expect(screen.getByText(`Open Runway on a computer to ${what}.`)).toBeInTheDocument();
    expect(api).not.toHaveBeenCalledWith("/api/investments?period=1Y");
  });

  it("lists its accounts and tabs on a computer", async () => {
    render(NetWorth);
    expect(await screen.findByRole("button", { name: "Checking, $5,500.00" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Net worth" })).getAllByRole("link")).toHaveLength(4);
    expect(screen.getByText(/Not counted/)).toBeInTheDocument();
  });
});
