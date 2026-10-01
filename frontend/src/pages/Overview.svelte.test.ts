// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { forecastSheet } from "$lib/components/overview/forecastSheet.svelte";
import type { Overview as OverviewData } from "$lib/types";
import Overview from "./Overview.svelte";

const TODAY = "2026-09-30";
const fc = (extra: Partial<OverviewData> = {}): OverviewData => ({
  today: TODAY, dates: [TODAY, "2026-10-01"], total: [1000, 900], low: { date: "2026-10-01", balance: 900 },
  accounts: [{ id: "chk", name: "Checking", kind: "checking", balance: 1000, balance_date: TODAY, daily_spend: 0, daily_spend_on: false, daily_spend_estimate: 30 }],
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

  it("says what the verdict leaves out, and turns everyday spending on in place", async () => {
    let on = false;
    serve(() => fc({ accounts: [{ ...fc().accounts[0], daily_spend_on: on, daily_spend: on ? 30 : 0 }] }));
    const user = userEvent.setup();
    render(Overview);
    expect(await screen.findByText(/Includes 1 bill\. Everyday spending isn’t included/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    const box = await screen.findByRole("checkbox", { name: "Subtract average everyday spending" });
    on = true;
    await user.click(box);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/chk", { method: "POST", body: { daily_spend: 1 } }));
    // The page loads its figures again without being drawn afresh, so the sheet stays open.
    expect(await screen.findByText(/Includes 1 bill and everyday spending of about \$30 a day/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Forecast settings" })).toBeInTheDocument();
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
