// @vitest-environment jsdom
// Settings → Assumptions: every explanation in one place, and each page's ⓘ link to its group.
import { cleanup, render, screen, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/categories.svelte", () => ({ loadCategories: vi.fn(async () => {}), categories: { list: [] }, catLabel: (x: string) => x, catLook: () => ({ color: "#888" }), categoryGroups: () => [], catParentOf: () => null }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { ASSUMPTION_GROUPS, DISCLAIMER } from "$lib/components/settings/assumptionGroups";
import { viewport } from "$lib/phone.svelte";
import Budget from "./Budget.svelte";
import NetWorth from "./NetWorth.svelte";
import Reports from "./Reports.svelte";
import Settings from "./Settings.svelte";
import Transactions from "./Transactions.svelte";

const settingsData = (async (path: string) => (path === "/api/accounts" || path === "/api/rules" ? [] : {})) as never;
const link = () => screen.getByRole("link", { name: "Assumptions" });

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(() => new Promise(() => {}) as never);   // pages just keep loading: the title row is what's tested
  app.state = { connected: true, brands: {} } as never;
  location.hash = "";
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); viewport.phone = false; });

describe("Settings → Assumptions", () => {
  it("lists every group, with the disclaimer once at the top", async () => {
    vi.mocked(api).mockImplementation(settingsData);
    render(Settings, { sub: "assumptions" });
    expect(await screen.findByText(DISCLAIMER)).toBeInTheDocument();
    for (const title of ["Forecast", "Budget & spending", "Transactions & categories", "Net worth", "Investments", "Retirement", "Equity", "Churning", "AI"]) {
      expect(screen.getByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    }
    expect(ASSUMPTION_GROUPS.map((g) => g.id)).toEqual(["forecast", "budget", "transactions", "networth", "investments", "retirement", "equity", "churning", "ai"]);
    expect(screen.getAllByText(/advice/)).toHaveLength(2);   // the disclaimer, once, and Churning’s 1099 note: nowhere else
    const tabs = screen.getByRole("navigation", { name: "Settings" });
    expect(within(tabs).getByRole("link", { name: "Assumptions" })).toHaveAttribute("aria-current", "page");
  });

  it("has what the pages used to explain: equity's valuation, the retirement spending rule, the AI's data", async () => {
    vi.mocked(api).mockImplementation(settingsData);
    render(Settings, { sub: "assumptions" });
    expect(await screen.findByText(/Only what has vested counts toward net worth, at each company’s latest share price/)).toBeInTheDocument();
    expect(screen.getByText(/Savings stop at each person’s retirement/)).toBeInTheDocument();
    expect(screen.getByText(/Only the date, amount, merchant and account type of each transaction are sent/)).toBeInTheDocument();
    expect(screen.getByText(/The projection runs once in today’s dollars/)).toBeInTheDocument();
    expect(screen.getByText(/Runway’s spending figure is the average of the last six full months/)).toBeInTheDocument();
    expect(screen.getByText(/Sale proceeds are before selling costs/)).toBeInTheDocument();
    expect(screen.getByText(/homes you don’t keep updated are valued by hand/)).toBeInTheDocument();
  });

  it("scrolls to the group the address names", async () => {
    vi.mocked(api).mockImplementation(settingsData);
    location.hash = "#setup/assumptions/retirement";
    render(Settings, { sub: "assumptions" });
    await screen.findByText(DISCLAIMER);
    await vi.waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    const scrolled = vi.mocked(Element.prototype.scrollIntoView).mock.contexts[0] as HTMLElement;
    expect(scrolled.id).toBe("assumptions-retirement");
  });

  it("is reachable on a phone, where the rest of Settings isn't", async () => {
    viewport.phone = true;
    vi.mocked(api).mockImplementation(settingsData);
    render(Settings, { sub: "assumptions" });
    expect(await screen.findByText(DISCLAIMER)).toBeInTheDocument();
    expect(screen.queryByText(/Open Runway on a computer to/)).not.toBeInTheDocument();
  });
});

describe("a page's link to its assumptions", () => {
  it("goes to the right group, one per page", () => {
    app.state = { connected: false } as never;
    const pages: [string, () => unknown, string][] = [
      ["Budget", () => render(Budget), "budget"],
      ["Reports", () => render(Reports), "budget"],
      ["Transactions", () => render(Transactions), "transactions"],
      ["Net worth", () => render(NetWorth), "networth"],
      ["Net worth equity", () => render(NetWorth, { sub: "equity" }), "equity"],
      ["Net worth investments", () => render(NetWorth, { sub: "investments" }), "investments"],
      ["Net worth retirement", () => render(NetWorth, { sub: "retirement" }), "retirement"],
    ];
    for (const [, show, group] of pages) {
      show();
      expect(screen.getAllByRole("link", { name: "Assumptions" })).toHaveLength(1);
      expect(link()).toHaveAttribute("href", `#setup/assumptions/${group}`);
      expect(ASSUMPTION_GROUPS.some((g) => g.id === group)).toBe(true);
      cleanup();
    }
  });
});
