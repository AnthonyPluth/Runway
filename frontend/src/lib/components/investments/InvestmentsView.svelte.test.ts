// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { holding } from "../../../test/fixtures";
import InvestmentsView from "./InvestmentsView.svelte";
import { inv } from "./state.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); inv.period = "1Y"; });

describe("InvestmentsView", () => {
  it("with no investment accounts, asks you to connect one, with a link to Connections", async () => {
    vi.mocked(api).mockResolvedValue({ inv_accounts: 0, items: [] } as never);
    render(InvestmentsView);
    const block = await screen.findByTestId("getting-started");
    expect(within(block).getByText(/Connect a brokerage or retirement account/)).toBeInTheDocument();
    expect(within(block).getByText(/enter its holdings by hand/)).toBeInTheDocument();
    expect(within(block).queryByText(/holdings, allocation, dividends and activity/)).toBeNull();
    expect(within(block).getByRole("link", { name: "Connect an investment account" })).toHaveAttribute("href", "#setup/connections");
    expect(screen.queryByText("No investment accounts yet")).toBeNull();
  });

  it("has no Accounts section or box to leave an account out (Settings → Accounts is the only way), and shows holdings' logos", async () => {
    const perf = { start: "2026-08-01", return: 0.1, benchmark_return: 0.08, gain: 100 };
    const data = {
      today: "2026-09-30", total: 2500, unrealized_gain: 500, cost_basis: 2000, day_change: 12, day_change_pct: 0.005, cost_missing: 0, cost_missing_value: 0,
      holdings: [holding({ logo: "/api/merchants/ticker%3AVTI/logo" }), holding({ security_id: "s2", ticker: "XYZ", name: "XYZ Corp", logo: null })],
      allocation: { asset_class: [], account: [], sector: [], holding: [] }, income: { months: [], income: [], fees: [], income_12m: 0, fees_12m: 0 },
      history: { dates: ["2026-08-01", "2026-09-30"], value: [2000, 2500], flows: [0, 0], invested: [2000, 2000], twr: [0, 0.1], benchmark: [0, 0.08], missing_prices: [], estimated_before: null },
      performance: perf, periods: { "1M": perf }, xray: [], plan: {},
      accounts: [{ id: "a1", item_id: "i", name: "Roth IRA", hidden: 0, hidden_in_accounts: 0, source: "plaid", institution_name: "Fidelity", tracked: 0, drift: null, balance: 2500 }],
    };
    vi.mocked(api).mockImplementation((async (path: string) => path.startsWith("/api/investments?") ? data : { inv_accounts: 1, items: [] }) as never);
    const { container } = render(InvestmentsView);
    await screen.findByText("Holdings");
    expect(screen.queryByText("Accounts")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Show .* again/ })).toBeNull();
    expect(container.querySelector('#inv-holdings img[src="/api/merchants/ticker%3AVTI/logo"]')).not.toBeNull();   // a holding's logo
    expect(container.querySelectorAll("#inv-holdings img")).toHaveLength(1);                                        // the other keeps its letter
    expect(vi.mocked(api).mock.calls.some((c) => String(c[0]).startsWith("/api/plaid/accounts/"))).toBe(false);
  });

  it("shows its totals as an unboxed strip, with Checks (not X-ray) and no placeholder lines for empty lists", async () => {
    const perf = { start: "2026-08-01", return: 0.1, benchmark_return: 0.08, gain: 100 };
    const data = {
      today: "2026-09-30", total: 2500, unrealized_gain: 500, cost_basis: 2000, day_change: -12, day_change_pct: -0.005, cost_missing: 2, cost_missing_value: 300,
      holdings: [holding()], allocation: { asset_class: [], account: [], sector: [], holding: [] }, income: { months: [], income: [], fees: [], income_12m: 0, fees_12m: 0 },
      history: { dates: ["2026-08-01", "2026-09-30"], value: [2000, 2500], flows: [0, 0], invested: [2000, 2000], twr: [0, 0.1], benchmark: [0, 0.08], missing_prices: [], estimated_before: null },
      performance: perf, periods: { "1M": perf }, xray: [], plan: {},
      accounts: [{ id: "a1", item_id: "i", name: "Roth IRA", hidden: 0, hidden_in_accounts: 0, source: "plaid", institution_name: "Fidelity", tracked: 0, drift: null, balance: 2500 }],
    };
    vi.mocked(api).mockImplementation((async (path: string) => path.startsWith("/api/investments?") ? data : { inv_accounts: 1, items: [] }) as never);
    const { container } = render(InvestmentsView);
    await screen.findByText("Holdings");
    expect(container.querySelector("dl")).not.toBeNull();   // StatStrip
    for (const l of ["Total value", "Today", "Total gain", "Return · 1Y"]) expect(screen.getByText(l, { selector: "dt span" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /2 holdings .* need a cost basis/ })).toBeInTheDocument();
    expect(screen.getByText("Checks", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
    expect(screen.queryByText("X-ray")).toBeNull();
    expect(screen.queryByText("Nothing to show.")).toBeNull();
  });

  describe("holdings entered by hand", () => {
    const perf = { start: "2026-08-01", return: 0, benchmark_return: 0, gain: 0 };
    const acct = (over: object) => ({ id: "sf:vw", item_id: "sf", name: "Vestwell 401k", hidden: 0, hidden_in_accounts: 0, source: "simplefin", institution_name: "Vestwell", tracked: 0, drift: null, balance: 20000, ...over });
    const page = (accounts: object[], seen: object[]) => {
      const data = {
        today: "2026-09-30", total: 20000, unrealized_gain: 0, cost_basis: 0, day_change: 0, day_change_pct: 0, cost_missing: 0, cost_missing_value: 0, holdings: [],
        allocation: { asset_class: [], account: [], sector: [], holding: [] }, income: { months: [], income: [], fees: [], income_12m: 0, fees_12m: 0 },
        history: { dates: ["2026-08-01"], value: [1], flows: [0], invested: [1], twr: [0], benchmark: [0], missing_prices: [], estimated_before: null },
        performance: perf, periods: { "1M": perf }, xray: [], plan: {}, accounts,
      };
      vi.mocked(api).mockImplementation((async (path: string) =>
        path.startsWith("/api/investments?") ? data
        : path.startsWith("/api/tracked/") ? { positions: [], contributions: [] }
        : { inv_accounts: 1, items: [], simplefin_seen: seen }) as never);
    };

    it("offers 'Enter holdings' for a balance-only account and opens the editor", async () => {
      page([acct({})], [{ id: "vw", name: "Vestwell 401k", positions: 0, fields: [] }]);
      render(InvestmentsView);
      const line = await screen.findByTestId("hand-tracked");
      expect(line).not.toHaveAttribute("open");   // folded away at the bottom
      expect(screen.getByTestId("hand-tracked-summary")).toHaveTextContent("1 account");
      expect(line).toHaveTextContent("Vestwell 401k · balance only ·");
      await fireEvent.click(within(line).getByRole("button", { name: "Enter holdings" }));
      expect(await screen.findByText("What this account holds")).toBeInTheDocument();
      expect(vi.mocked(api)).toHaveBeenCalledWith("/api/tracked/sf%3Avw");
    });

    it("offers 'Edit holdings' for one kept by hand, with the drift warning when the funds are off", async () => {
      page([acct({ tracked: 2, drift: 0.123 })], [{ id: "vw", name: "Vestwell 401k", positions: 0, fields: [] }]);
      render(InvestmentsView);
      const line = await screen.findByTestId("hand-tracked");
      expect(line).toHaveTextContent("entered by hand");
      expect(within(line).getByRole("button", { name: "Edit holdings" })).toBeInTheDocument();
      expect(within(line).getByText(/12\.3% off the synced balance/)).toBeInTheDocument();
      expect(screen.getByTestId("hand-tracked-summary")).toHaveTextContent("1 account · 1 to update");
    });

    it("shows no drift warning when the funds match, and no line for an account SimpleFIN sends positions for", async () => {
      page([acct({ tracked: 2, drift: 0.01 }), acct({ id: "sf:wf", name: "Roth IRA" })], [{ id: "wf", name: "Roth IRA", positions: 3, fields: [] }]);
      render(InvestmentsView);
      const line = await screen.findByTestId("hand-tracked");
      expect(within(line).queryByText(/off the synced balance/)).toBeNull();
      expect(line).not.toHaveTextContent("Roth IRA");
    });
  });

  describe("its figures and states", () => {
    const perf = { start: "2026-08-01", return: 0.1, benchmark_return: 0.1, gain: 100 };
    const page = (over: object = {}) => ({
      today: "2026-09-30", total: 2500, unrealized_gain: -500, cost_basis: 3000, day_change: 12, day_change_pct: 0.005, cost_missing: 0, cost_missing_value: 0,
      holdings: [holding()], allocation: { asset_class: [{ name: "Stocks", value: 2500, share: 0.333 }], account: [], sector: [], holding: [] },
      income: { months: [], income: [], fees: [], income_12m: 0, fees_12m: 0 },
      history: { dates: ["2026-08-01", "2026-09-30"], value: [2000, 2500], flows: [0, 0], invested: [2000, 2000], twr: [0, 0.1], benchmark: [0, 0.1], missing_prices: [], estimated_before: null },
      performance: perf, periods: { "1M": { ...perf, gain: -40 } }, xray: [], plan: {}, accounts: [],
      ...over,
    });
    const tile = (label: string) => screen.getByText(label, { selector: "dt span" }).closest("div")!;
    afterEach(() => vi.useRealTimers());
    // Live prices are left out (they'd re-price the page from its holdings): their endpoint doesn't answer here.
    const serve = (f: (path: string) => unknown) => vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/investments/live") throw new Error("no live prices in tests");
      return f(path);
    }) as never);

    it("colors gains and losses as gains and losses, not as warnings, each signed with a real minus", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page() : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      expect(tile("Today")).toHaveAttribute("data-tone", "up");
      expect(tile("Total gain")).toHaveAttribute("data-tone", "down");
      expect(within(tile("Total gain")).getByText("−$500.00")).toHaveClass("text-loss");
      expect(tile("Total gain")).toHaveTextContent("−16.7% on $3,000 cost basis");
      expect(tile("Return · 1Y")).toHaveTextContent("S&P 500 +10.0% · level with it");   // not "ahead by .0%"
      expect(screen.getByText("−$40.00")).toHaveClass("text-loss");   // the period table's gain
      expect(screen.getByText("Gain", { selector: "td" })).toBeInTheDocument();
      expect(screen.getByText("33%", { selector: "td" })).toBeInTheDocument();   // allocation, as a whole percentage
    });

    it("says nothing about a total gain it doesn't know, rather than a broken percentage", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page({ unrealized_gain: null }) : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      expect(tile("Total gain")).toHaveTextContent(/^Total gain —$/);
      expect(document.body.textContent).not.toMatch(/NaN/);
    });

    it("names a connection's problem in words, with the way to fix it", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page()
        : { inv_accounts: 1, items: [{ item_id: "i1", institution_name: "Fidelity", error: "ITEM_LOGIN_REQUIRED" }, { item_id: "i2", institution_name: "Schwab", error: "INSTITUTION_DOWN" }] });
      render(InvestmentsView);
      expect(await screen.findByText(/Fidelity: Your bank needs you to sign in again\./)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Reconnect in Settings" })).toHaveAttribute("href", "#setup/connections");
      expect(screen.getByText(/Schwab: The bank isn’t answering right now/)).toBeInTheDocument();
      expect(screen.queryByText(/ITEM_LOGIN_REQUIRED|INSTITUTION_DOWN/)).toBeNull();
    });

    it("marks holdings more than two days old in the warning color", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date(2026, 8, 30, 12));
      const status = (last: string) => ({ inv_accounts: 1, items: [], last_inv_sync: last });
      let last = "2026-09-29T07:00:00";
      serve((path: string) => path.startsWith("/api/investments?") ? page() : status(last));
      const { unmount } = render(InvestmentsView);
      expect(await screen.findByTestId("holdings-updated")).not.toHaveClass("text-warning");
      unmount();
      last = "2026-09-27T07:00:00";
      render(InvestmentsView);
      expect(await screen.findByTestId("holdings-updated")).toHaveClass("text-warning");
    });

    it("keeps the numbers and the period when another period fails to load, with Retry", async () => {
      let fail = false;
      serve((path: string) => {
        if (path.startsWith("/api/investments?")) { if (fail) throw new Error("Server down"); return page({ total: path.endsWith("3M") ? 2600 : 2500 }); }
        return { inv_accounts: 1, items: [] };
      });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      fail = true;
      await fireEvent.click(screen.getByRole("radio", { name: "3M" }));
      const banner = await screen.findByTestId("refresh-failed");
      expect(banner).toHaveTextContent("Couldn’t refresh");
      expect(inv.period).toBe("1Y");
      await waitFor(() => expect(screen.getByRole("radio", { name: "1Y" })).toHaveAttribute("data-state", "on"));   // the picker goes back
      expect(screen.getByText("Return · 1Y", { selector: "dt span" })).toBeInTheDocument();
      expect(tile("Total value")).toHaveTextContent("$2,500");
      fail = false;
      await fireEvent.click(within(banner).getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(screen.queryByTestId("refresh-failed")).toBeNull());
      expect(tile("Total value")).toHaveTextContent("$2,500");   // Retry reloads what's shown
      await fireEvent.click(screen.getByRole("radio", { name: "3M" }));
      await waitFor(() => expect(tile("Total value")).toHaveTextContent("$2,600"));
      expect(inv.period).toBe("3M");
      expect(screen.getByText("Return · 3M", { selector: "dt span" })).toBeInTheDocument();
    });
  });
});
