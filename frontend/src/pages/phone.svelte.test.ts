// @vitest-environment jsdom
// The pages on a phone: what they keep, and the note where a computer's part would be. A computer sees them as before.
import { cleanup, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, catLook: () => ({ color: "#888" }), categoryGroups: () => [], catParentOf: () => null }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { benefit, card, churning } from "$lib/components/churning/fixtures";
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

  it("has every tab on a phone, and opens the one the link names", async () => {
    viewport.phone = true;
    vi.mocked(api).mockImplementation(data);
    render(Settings, { sub: "rules" });
    const tabs = await screen.findByRole("navigation", { name: "Settings" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent!.trim().replace(/\d+$/, ""))).toEqual(
      ["Accounts", "Connections", "Categories", "Rules", "Notifications", "Advanced"]);
    expect(await screen.findByText("Rules", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
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

  it("has all five reports on a phone, and the one the link names", async () => {
    viewport.phone = true;
    vi.mocked(api).mockImplementation((async (p: string) => (String(p).includes("merchant") ? { merchants: [] } : {})) as never);
    render(Reports, { sub: "merchants" });
    expect(within(await screen.findByRole("navigation", { name: "Reports" })).getAllByRole("link")).toHaveLength(5);
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
    await vi.waitFor(() => expect(vi.mocked(api).mock.calls.some(([p]) => String(p).startsWith("/api/merchants") || String(p).includes("merchant"))).toBe(true));
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

  it("has cards, bank bonuses, plans and to-dos on a phone", async () => {
    viewport.phone = true;
    data();
    const { rerender } = render(Churning, { sub: "" });
    expect(await screen.findByRole("button", { name: "Add a to-do" })).toBeInTheDocument();   // the Overview: to-dos and plans
    expect(screen.getByText("Planned")).toBeInTheDocument();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Churning sections" })).toBeInTheDocument();
    await rerender({ sub: "bank" });
    expect(await screen.findByRole("button", { name: "Add a bank bonus" })).toBeInTheDocument();
  });

  it("shows the cards tab on a phone", async () => {
    viewport.phone = true;
    data();
    render(Churning, { sub: "cards" });
    expect(await screen.findByRole("button", { name: "Add a card" })).toBeInTheDocument();
  });

  it("has its cards and tabs on a computer", async () => {
    data();
    render(Churning, { sub: "cards" });
    expect(await screen.findByRole("button", { name: "Add a card" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Churning sections" })).toBeInTheDocument();
  });
});

describe("Net worth", () => {
  const summary = {
    today: "2026-09-30", net: 5000, assets: 5500, liabilities: 500, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
    assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [{ id: "x", name: "Old card", org: null, kind: "credit", balance: -50 }],
    groups: [{ key: "cash", label: "Cash", side: "asset", total: 5500, items: [{ type: "account", id: "a1", name: "Checking", value: 5500, org: "Bank" }] }],
  };
  beforeEach(() => { vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/networth" ? summary : {})) as never); });

  it("has the assets and liabilities to edit, and all four tabs, on a phone", async () => {
    viewport.phone = true;
    render(NetWorth);
    expect(await screen.findByText("$5,000")).toBeInTheDocument();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Checking, $5,500.00" })).toBeInTheDocument();
    expect(screen.getByText(/Not counted/)).toBeInTheDocument();
    const tabs = screen.getByRole("navigation", { name: "Net worth" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent!.trim())).toEqual(["Summary", "Investments", "Equity", "Retirement"]);
  });

  it.each(["investments", "equity"])("shows %s on a phone, with no note about a computer", async (sub) => {
    viewport.phone = true;
    render(NetWorth, { sub });
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
    await vi.waitFor(() => expect(vi.mocked(api).mock.calls.some(([p]) => String(p).startsWith(sub === "equity" ? "/api/equity" : "/api/plaid/status"))).toBe(true));
  });

  it("lists its accounts and tabs on a computer", async () => {
    render(NetWorth);
    expect(await screen.findByRole("button", { name: "Checking, $5,500.00" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Net worth" })).getAllByRole("link")).toHaveLength(4);
    expect(screen.getByText(/Not counted/)).toBeInTheDocument();
  });
});
