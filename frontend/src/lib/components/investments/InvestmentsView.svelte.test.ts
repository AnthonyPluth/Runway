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

  describe("asks for the status and the portfolio at once", () => {
    it("sends both requests before the status has answered", async () => {
      vi.mocked(api).mockImplementation((() => new Promise(() => {})) as never);
      render(InvestmentsView);
      await Promise.resolve();
      expect(vi.mocked(api).mock.calls.map((c) => c[0])).toEqual(["/api/investments?period=1Y", "/api/plaid/status"]);
    });

    it("with no investment accounts, a failing portfolio request changes nothing", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => {
        if (path.startsWith("/api/investments?")) throw new Error("boom");
        return { inv_accounts: 0, items: [] };
      }) as never);
      render(InvestmentsView);
      expect(await screen.findByTestId("getting-started")).toBeInTheDocument();
      expect(screen.queryByText(/Something went wrong/)).toBeNull();
    });

    it("with investment accounts, a failing portfolio request is the page's error, as before", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => {
        if (path.startsWith("/api/investments?")) throw new Error("boom");
        return { inv_accounts: 1, items: [] };
      }) as never);
      render(InvestmentsView);
      expect(await screen.findByText("Something went wrong: boom")).toBeInTheDocument();
    });

    it("a failing status is the page's error, whatever the portfolio says", async () => {
      vi.mocked(api).mockImplementation((async (path: string) => {
        if (path === "/api/plaid/status") throw new Error("no status");
        return {};
      }) as never);
      render(InvestmentsView);
      expect(await screen.findByText("Something went wrong: no status")).toBeInTheDocument();
    });
  });

  it("has no box to leave an account out (Settings → Accounts is the only way), and shows holdings' logos", async () => {
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
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Show .* again/ })).toBeNull();
    expect(container.querySelector('#inv-holdings img[src="/api/merchants/ticker%3AVTI/logo"]')).not.toBeNull();
    expect(container.querySelectorAll("#inv-holdings img")).toHaveLength(1);
    expect(vi.mocked(api).mock.calls.some((c) => String(c[0]).startsWith("/api/plaid/accounts/"))).toBe(false);
  });

  it("shows its totals as an unboxed strip, with no X-ray and no placeholder lines for empty lists", async () => {
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
    expect(container.querySelector("dl")).not.toBeNull();
    expect(screen.getByTestId("headline")).toHaveTextContent("Total value · 1 account");
    for (const l of ["Today", "Total gain", "Return · 1Y", "Dividends · 12 mo"]) expect(screen.getByText(l, { selector: "dt span" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /2 holdings .* need a cost basis/ })).toBeInTheDocument();
    expect(screen.queryByText("Checks")).toBeNull();
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
      expect(line).not.toHaveAttribute("open");
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
    const serve = (f: (path: string) => unknown) => vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/investments/live") throw new Error("no live prices in tests");
      return f(path);
    }) as never);

    describe("allocation, checks and accounts", () => {
      const names = (n: number) => Array.from({ length: n }, (_, i) => ({ name: `Group ${i + 1}`, value: (n - i) * 100, share: (n - i) / 100 }));
      const load = async (over: object) => {
        serve((path: string) => path.startsWith("/api/investments?") ? page(over) : { inv_accounts: 1, items: [], simplefin_seen: [] });
        render(InvestmentsView);
        await screen.findByText("Holdings");
      };
      const legend = () => within(screen.getByTestId("alloc-legend")).getAllByTestId("alloc-row").map((r) => r.textContent?.replace(/\s+/g, " ").trim() ?? "");

      it("lists up to six groups as they are, in a bar of the same number of segments", async () => {
        await load({ allocation: { asset_class: names(6), account: [], sector: [], holding: [] } });
        expect(legend()).toHaveLength(6);
        expect(legend().join()).not.toContain("Other");
        expect(screen.getByTestId("alloc-bar").children).toHaveLength(6);
      });

      it("folds the groups past six into the five largest and Other", async () => {
        await load({ allocation: { asset_class: names(8), account: [], sector: [], holding: [] } });
        const rows = legend();
        expect(rows).toHaveLength(6);
        expect(rows[0]).toContain("Group 1");
        expect(rows[4]).toContain("Group 5");
        expect(rows[5]).toMatch(/^Other 6%/);
        expect(rows[5]).toContain("$600");
        expect(screen.getByTestId("alloc-bar").children).toHaveLength(6);
      });

      it("picks what to group by from a menu in the card's header", async () => {
        await load({ allocation: { asset_class: names(2), account: [{ name: "Roth IRA", value: 5, share: 1 }], sector: [], holding: [] } });
        expect(screen.queryByRole("radio", { name: "Account" })).toBeNull();
        await fireEvent.click(screen.getByRole("button", { name: "Allocation by: Asset class" }));
        await fireEvent.click(await screen.findByRole("button", { name: "Account" }));
        expect(inv.allocTab).toBe("account");
        expect(legend()).toEqual([expect.stringContaining("Roth IRA")]);
        inv.allocTab = "asset_class";
      });

      it("shows only the checks that aren't fine, as cards, with a note in the muted color", async () => {
        await load({ xray: [
          { name: "Concentration", ok: false, detail: "One fund is most of it." },
          { name: "Fees", ok: true, detail: "Low." },
          { name: "Cash drag", ok: false, info: true, detail: "Two holdings lack one." },
        ] });
        expect(screen.getAllByTestId("check-card")).toHaveLength(2);
        expect(within(screen.getByTestId("worth-a-look")).getByText("Worth a look", { selector: "[data-slot=card-title]" })).toBeInTheDocument();
        expect(screen.getByText("Concentration")).toHaveClass("text-warning");
        expect(screen.getByText("Cash drag")).toHaveClass("text-muted-foreground");
        expect(screen.getByText("Two holdings lack one.")).toBeInTheDocument();
        expect(screen.queryByText("Fees")).toBeNull();
        expect(screen.queryByTestId("checks-fine")).toBeNull();
      });

      it("says all checks look fine, instead of the card, when none is failing", async () => {
        await load({ xray: [{ name: "Fees", ok: true, detail: "Low." }] });
        expect(screen.getByTestId("checks-fine")).toHaveTextContent("All checks look fine");
        expect(screen.queryByTestId("worth-a-look")).toBeNull();
        expect(screen.queryByTestId("check-card")).toBeNull();
      });

      it("links each account by the id Settings knows it by: a Plaid account's account_id, a SimpleFIN one's id without sf:", async () => {
        const acct = (over: object) => ({ item_id: "i", name: "Acct", hidden: 0, hidden_in_accounts: 0, institution_name: null, tracked: 0, drift: null, balance: 10, ...over });
        await load({ accounts: [
          acct({ id: "inv_1", source: "plaid", account_id: "plaid_acc_1", name: "Plaid one" }),
          acct({ id: "sf:ACT-9", source: "simplefin", name: "SimpleFIN one" }),
          acct({ id: "inv_2", source: "plaid", account_id: null, name: "Unmatched" }),
        ] });
        expect(screen.getAllByTestId("account-row").map((r) => r.getAttribute("href"))).toEqual([
          "#setup/accounts?account=plaid_acc_1", "#setup/accounts?account=ACT-9", "#setup/accounts"]);
      });

      it("falls back to the value of an account's lots when it has no balance", async () => {
        const lot = { account_id: "a1", security_id: "s1", account_name: "", quantity: 4, value: 1000, cost_basis: null, reported_cost_basis: null, per_share: null, manual: false };
        await load({
          holdings: [holding({ quantity: 4, day_change: 8, lots: [lot] })],
          accounts: [{ id: "a1", item_id: "i", name: "Roth IRA", hidden: 0, hidden_in_accounts: 0, source: "plaid", institution_name: null, tracked: 0, drift: null, balance: null }],
        });
        expect(screen.getByTestId("account-row")).toHaveTextContent("$1,000.00");
      });

      it("lists each visible account with its value and today's change, signed, linking to it", async () => {
        const lot = (account_id: string, quantity: number) => ({ account_id, security_id: "s1", account_name: "", quantity, value: quantity * 250, cost_basis: null, reported_cost_basis: null, per_share: null, manual: false });
        const acct = (id: string, name: string, balance: number, hidden = 0) => ({ id, account_id: `plaid_${id}`, item_id: "i", name, hidden, hidden_in_accounts: 0, source: "plaid", institution_name: "Fidelity", tracked: 0, drift: null, balance });
        await load({
          holdings: [holding({ quantity: 10, day_change: 12, lots: [lot("a1", 5), lot("a2", 5)] }), holding({ security_id: "s2", quantity: 4, day_change: -8, lots: [lot("a2", 4)] })],
          accounts: [acct("a1", "Roth IRA", 1250), acct("a2", "Brokerage", 2500), acct("a3", "Old 401k", 100, 1)],
        });
        const rows = screen.getAllByTestId("account-row");
        expect(rows).toHaveLength(2);
        expect(rows[0]).toHaveTextContent("Roth IRA");
        expect(rows[0]).toHaveTextContent("$1,250.00");
        expect(rows[0]).toHaveAttribute("href", "#setup/accounts?account=plaid_a1");
        expect(within(rows[0]).getByTestId("account-day")).toHaveTextContent("+$6.00 today");
        expect(within(rows[0]).getByTestId("account-day")).toHaveClass("text-good");
        expect(within(rows[1]).getByTestId("account-day")).toHaveTextContent("−$2.00 today");
        expect(within(rows[1]).getByTestId("account-day")).toHaveClass("text-loss");
        expect(screen.queryByText("Old 401k")).toBeNull();
      });
    });

    it("colors gains and losses as gains and losses, not as warnings, each signed with a real minus", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page() : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      expect(tile("Today")).toHaveAttribute("data-tone", "up");
      expect(tile("Total gain")).toHaveAttribute("data-tone", "down");
      expect(within(tile("Total gain")).getByText("−$500.00")).toHaveClass("text-loss");
      expect(tile("Total gain")).toHaveTextContent("−16.7% on $3,000 cost basis");
      expect(tile("Return · 1Y")).toHaveTextContent("S&P 500 +10.0%");
      expect(screen.getByTestId("vs-bench-chip")).toHaveTextContent("level with the S&P 500");
      expect(screen.getByText("−$40.00")).toHaveClass("text-loss");
      expect(screen.getByText("Gain", { selector: "td" })).toBeInTheDocument();
      expect(screen.getByText("33%", { selector: "[data-testid=alloc-legend] span" })).toBeInTheDocument();
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

    const chip = async (ret: number, bench: number) => {
      const perf = { start: "2026-08-01", return: ret, benchmark_return: bench, gain: 100 };
      serve((path: string) => path.startsWith("/api/investments?") ? page({ performance: perf }) : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      return screen.getByTestId("vs-bench-chip");
    };
    it("says how many points ahead of the S&P 500 the period is", async () => {
      const c = await chip(0.1106, 0.1);
      expect(c).toHaveTextContent("+1.06 pts vs S&P 500");
      expect(c).toHaveClass("text-good");
      expect(screen.getByTestId("period-chip")).toHaveTextContent("▲ +$100.00 (+11.1%) 1Y");
    });
    it("says how many points behind it is, with a real minus", async () => {
      const c = await chip(0.0394, 0.05);
      expect(c).toHaveTextContent("−1.06 pts vs S&P 500");
      expect(c).toHaveClass("text-loss");
    });
    it("leaves the dollar change out of the period chip when it isn't known, rather than a placeholder", async () => {
      const perf = { start: "2026-08-01", return: 0.05, benchmark_return: 0.04, gain: null };
      serve((path: string) => path.startsWith("/api/investments?") ? page({ performance: perf }) : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      const c = screen.getByTestId("period-chip");
      expect(c).toHaveTextContent("▲ +5.0% 1Y");
      expect(c).not.toHaveTextContent("—");
    });
    it("says level when the gap rounds to nothing", async () => {
      expect(await chip(0.10004, 0.1)).toHaveTextContent("level with the S&P 500");
    });

    it("shows the value chart first and the return chart when chosen, in one card", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page() : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      expect(screen.getByTestId("chart-value")).toHaveTextContent("Net deposits");
      expect(screen.queryByTestId("chart-return")).toBeNull();
      expect(screen.getByTestId("chart-legend-line")).toHaveTextContent("You +10.0% · S&P 500 +10.0%");
      await fireEvent.click(screen.getByRole("radio", { name: "Return vs S&P 500" }));
      expect(screen.getByTestId("chart-return")).toHaveTextContent("Your portfolio");
      expect(screen.queryByTestId("chart-value")).toBeNull();
      await fireEvent.click(screen.getByRole("radio", { name: "Value" }));
      expect(screen.getByTestId("chart-value")).toBeInTheDocument();
    });

    it("offers 1M, 3M, YTD, 1Y, 2Y and All, and All asks for the whole history (period=MAX)", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page() : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      const period = screen.getByRole("group", { name: "Period" });
      expect(within(period).getAllByRole("radio").map((r) => r.textContent?.trim())).toEqual(["1M", "3M", "YTD", "1Y", "2Y", "All"]);
      await fireEvent.click(within(period).getByRole("radio", { name: "All" }));
      await waitFor(() => expect(inv.period).toBe("MAX"));
      expect(vi.mocked(api)).toHaveBeenCalledWith("/api/investments?period=MAX");
      expect(screen.getByText("Return · All", { selector: "dt span" })).toBeInTheDocument();
    });

    it("keeps the old numbers under “Couldn’t refresh” when the All period fails", async () => {
      serve((path: string) => {
        if (path.endsWith("MAX")) throw new Error("Server down");
        return path.startsWith("/api/investments?") ? page() : { inv_accounts: 1, items: [] };
      });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      await fireEvent.click(screen.getByRole("radio", { name: "All" }));
      expect(await screen.findByTestId("refresh-failed")).toHaveTextContent("Couldn’t refresh");
      expect(inv.period).toBe("1Y");
      expect(screen.getByTestId("headline-total")).toHaveTextContent("$2,500.00");
      expect(screen.getByText("Return · 1Y", { selector: "dt span" })).toBeInTheDocument();
    });

    it("shows dividends over 12 months with the fees under them", async () => {
      serve((path: string) => path.startsWith("/api/investments?") ? page({ income: { months: [], income: [], fees: [], income_12m: 480, fees_12m: 12.5 } }) : { inv_accounts: 1, items: [] });
      render(InvestmentsView);
      await screen.findByText("Holdings");
      expect(tile("Dividends · 12 mo")).toHaveTextContent("Dividends · 12 mo $480 fees $12.50");
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
      await waitFor(() => expect(screen.getByRole("radio", { name: "1Y" })).toHaveAttribute("data-state", "on"));
      expect(screen.getByText("Return · 1Y", { selector: "dt span" })).toBeInTheDocument();
      const total = () => screen.getByTestId("headline-total");
      expect(total()).toHaveTextContent("$2,500.00");
      fail = false;
      await fireEvent.click(within(banner).getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(screen.queryByTestId("refresh-failed")).toBeNull());
      expect(total()).toHaveTextContent("$2,500.00");
      await fireEvent.click(screen.getByRole("radio", { name: "3M" }));
      await waitFor(() => expect(total()).toHaveTextContent("$2,600.00"));
      expect(inv.period).toBe("3M");
      expect(screen.getByText("Return · 3M", { selector: "dt span" })).toBeInTheDocument();
    });
  });
});
