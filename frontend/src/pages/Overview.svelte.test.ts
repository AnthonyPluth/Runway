// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { viewport } from "$lib/phone.svelte";
import { forecastSheet } from "$lib/components/overview/forecastSheet.svelte";
import type { Overview as OverviewData } from "$lib/types";
import Overview from "./Overview.svelte";

const TODAY = "2026-09-30";
const fc = (extra: Partial<OverviewData> = {}): OverviewData => ({
  today: TODAY, dates: [TODAY, "2026-10-01"], total: [1000, 900], low: { date: "2026-10-01", balance: 900 },
  accounts: [{ id: "chk", name: "Checking", kind: "checking", balance: 1000, balance_date: TODAY }],
  events: [{ date: "2026-10-01", name: "Rent", amount: -100, kind: "recurring", key: "rec:1:2026-10-01", balance_after: 900, recurring_id: 1 }],
  cards: [], unlinked_cards: [], warnings: [], warning_links: [], missed: [], budget: null, ...extra,
});
// Overview's own figures; the rest of the page (This month) just keeps loading.
const serve = (data: () => OverviewData) => vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
  if (opts?.method) return { ok: true } as never;
  if (path.startsWith("/api/overview")) return data() as never;
  if (path === "/api/accounts") return [] as never;
  return new Promise(() => {}) as never;
});

const forecast = (days: number, balance: number): OverviewData => ({
  today: "2026-03-15", dates: Array.from({ length: days }, (_, i) => new Date(Date.UTC(2026, 2, 15 + i)).toISOString().slice(0, 10)),
  total: Array.from({ length: days }, () => balance), low: { date: "2026-03-15", balance },
  accounts: [{ id: "chk", name: "Everyday Checking", kind: "checking", balance }],
  events: [], cards: [], warnings: [], warning_links: [],
});

// jsdom doesn't lay out SVG; the chart measures its labels.
Object.assign(SVGElement.prototype, { getBBox: () => ({ x: 0, y: 0, width: 0, height: 0 }) });

beforeEach(() => {
  vi.mocked(api).mockReset();
  app.state = { connected: true, primary_account: "chk", horizon_days: 90, setup: { bank: true, primary: true, recurring: true, budgets: true, dismissed: false } };
});
afterEach(() => { cleanup(); forecastSheet.open = false; document.body.style.pointerEvents = ""; });

describe("Overview", () => {
  it("doesn't explain what the forecast counts", async () => {
    serve(() => fc());
    render(Overview);
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByText(/Includes 1 bill/)).not.toBeInTheDocument();
  });

  it("links each warning to where it's put right", async () => {
    serve(() => fc({
      warnings: ["Enter Visa’s latest statement so its payment is in the forecast.", "Amex: choose which account pays it in Settings."],
      warning_links: [{ text: "Enter Visa’s latest statement so its payment is in the forecast.", href: "#setup/accounts?account=visa" },
        { text: "Amex: choose which account pays it in Settings.", href: "#setup/accounts" }],
    }));
    render(Overview);
    expect(await screen.findByRole("link", { name: /Enter Visa’s latest statement/ })).toHaveAttribute("href", "/#setup/accounts?account=visa");
    expect(screen.getByRole("link", { name: /Amex: choose/ })).toHaveAttribute("href", "/#setup/accounts");
  });

  it("sends you to the forecast settings when there's no account to forecast", async () => {
    serve(() => fc({ accounts: [], total: [], events: [] }));
    render(Overview);
    expect(await screen.findByRole("link", { name: /No account to forecast yet/ })).toHaveAttribute("href", "/#overview?forecast");
  });

  it("says how much of the balance is pending", async () => {
    serve(() => fc({ accounts: [{ ...fc().accounts[0], balance: 625, pending: -375 }] }));
    const { unmount } = render(Overview);
    expect(await screen.findByText("Balance as of today, including −$375.00 pending")).toBeInTheDocument();
    unmount();
    serve(() => fc());
    render(Overview);
    expect(await screen.findByText("Balance as of today")).toBeInTheDocument();
  });

  it("says each figure once: no strip repeating the low, the end balance and the cards' total", async () => {
    serve(() => fc());
    render(Overview);
    expect(await screen.findByText(/stays above/)).toBeInTheDocument();
    expect(screen.queryByText(/^Lowest/)).not.toBeInTheDocument();
    expect(screen.queryByText("Owed on cards")).not.toBeInTheDocument();
    expect(screen.queryByText(/^In 90\sdays$/)).not.toBeInTheDocument();
  });

  it("says nothing about the tightest moment when it's today, and points no link at one account", async () => {
    serve(() => fc({ low: { date: TODAY, balance: 1000 }, events: [],
      accounts: [fc().accounts[0], { ...fc().accounts[0], id: "sav", name: "Savings", kind: "savings" }] }));
    render(Overview);
    expect(await screen.findByText(/stays above/)).toBeInTheDocument();
    expect(screen.queryByText(/tightest/)).not.toBeInTheDocument();
    expect(screen.getByText("2 accounts combined")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "choose one account" })).not.toBeInTheDocument();
  });

  it("draws one line, with no budget line or on-budget figures beside the balances", async () => {
    serve(() => fc({ events: [{ date: "2026-10-01", account_id: "chk", name: "Rent", amount: -200, kind: "recurring", key: "r", balance_after: 800 }],
      budget: { monthly: 500, used: [{ category: "Groceries", amount: 500, account_id: "chk", account: "Checking", chosen: true }], skipped: [] } }));
    render(Overview);
    expect(await screen.findByText(/Recurring bills and income, plus \$500 a month of budgeted spending/)).toBeInTheDocument();
    expect(screen.queryByText("If you stick to your budget")).not.toBeInTheDocument();
    expect(screen.queryByText(/on budget/)).not.toBeInTheDocument();
    expect(screen.queryByText("Day by day")).not.toBeInTheDocument();
  });

  it("points to Budget when there are no budgets for the forecast to spend", async () => {
    serve(() => fc({ budget: null }));
    render(Overview);
    expect(await screen.findByRole("link", { name: "set budgets" })).toHaveAttribute("href", "#budget");
  });

  it("says why a budget on a card paid from outside the forecast is left out", async () => {
    serve(() => fc({ budget: { monthly: 0, used: [],
      skipped: [{ category: "Groceries", reason: "its card isn't paid from a forecast account" }] } }));
    render(Overview);
    expect(await screen.findByTitle(/Left out: Groceries, whose card isn't paid from a forecast account\./)).toBeInTheDocument();
  });

  it("rounds a low the balance stays above down, and says a negative low to the cent", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/overview?days=90") return forecast(90, 4820.55) as never;
      return new Promise(() => {}) as never;
    });
    const { unmount } = render(Overview);
    expect(await screen.findByText(/stays above \$4,820 for 90\sdays/)).toBeInTheDocument();
    unmount();
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/overview?days=90") return forecast(90, -12.2) as never;
      return new Promise(() => {}) as never;
    });
    render(Overview);
    expect(await screen.findByText(/dips to -\$12\.20 today/)).toBeInTheDocument();
  });

  it("keeps the forecast on screen while another length loads", async () => {
    let answer: (fc: OverviewData) => void = () => {};
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/api/overview?days=90") return forecast(90, 1000) as never;
      if (path === "/api/overview?days=30") return new Promise((r) => { answer = r as typeof answer; }) as never;
      return new Promise(() => {}) as never;   // This month isn't under test
    });
    render(Overview);
    expect(await screen.findByText(/stays above \$1,000 for 90\sdays/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: "1M" }));
    expect(api).toHaveBeenCalledWith("/api/overview?days=30");
    // Still the 90-day forecast, not the loading placeholder, until the new one arrives.
    expect(screen.getByText(/stays above \$1,000 for 90\sdays/)).toBeInTheDocument();
    expect(document.querySelector("[aria-busy=true]")).toBeNull();

    answer(forecast(30, 2000));
    expect(await screen.findByText(/stays above \$2,000 for 30\sdays/)).toBeInTheDocument();
  });
});

describe("Overview on a phone", () => {
  afterEach(() => { cleanup(); viewport.phone = false; });

  it("links the no-account alert to the forecast settings", async () => {
    viewport.phone = true;
    serve(() => fc({ accounts: [], total: [], events: [] }));
    render(Overview);
    expect(await screen.findByRole("link", { name: /No account to forecast/ })).toHaveAttribute("href", "/#overview?forecast");
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
  });

  it("links alerts to their Settings fix", async () => {
    viewport.phone = true;
    serve(() => fc({ warning_links: [{ text: "Visa: choose which account pays it in Settings.", href: "#setup/accounts" },
      { text: "2 payments over $1,000 aren’t in the forecast.", href: "#recurring" }] }));
    render(Overview);
    expect(await screen.findByRole("link", { name: /Visa: choose which account/ })).toHaveAttribute("href", "/#setup/accounts");
    expect(screen.getByRole("link", { name: /2 payments over/ })).toHaveAttribute("href", "/#recurring");
    expect(screen.queryByText(/Open Runway on a computer/)).not.toBeInTheDocument();
  });

  it("opens the forecast settings sheet from the account name", async () => {
    viewport.phone = true;
    serve(() => fc());
    render(Overview);
    await userEvent.click(await screen.findByRole("button", { name: "Forecast settings" }));
    expect(await screen.findByLabelText("Default forecast length (days)")).toBeInTheDocument();
  });

  it("shows the setup checklist while steps are left", async () => {
    viewport.phone = true;
    app.state = { ...app.state!, setup: { bank: true, primary: true, recurring: false, budgets: false, dismissed: false } };
    serve(() => fc());
    render(Overview);
    expect(await screen.findByText("Finish setting up")).toBeInTheDocument();
  });

  it("welcomes a phone with the checklist before a bank is connected", async () => {
    viewport.phone = true;
    app.state = { ...app.state!, connected: false, setup: { bank: false, primary: false, recurring: false, budgets: false, dismissed: false } };
    serve(() => fc());
    render(Overview);
    expect(screen.getByText("Welcome to Runway")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect" })).toHaveAttribute("href", "#setup/connections");
  });
});
