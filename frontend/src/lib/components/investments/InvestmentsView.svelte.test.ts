// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { holding } from "../../../test/fixtures";
import InvestmentsView from "./InvestmentsView.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("InvestmentsView", () => {
  it("with no investment accounts, asks you to connect one, with a link to Connections", async () => {
    vi.mocked(api).mockResolvedValue({ inv_accounts: 0, items: [] } as never);
    render(InvestmentsView);
    const block = await screen.findByTestId("getting-started");
    expect(within(block).getByText(/Connect a brokerage or retirement account/)).toBeInTheDocument();
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
      performance: perf, periods: { "1M": perf }, xray: [], plan: {}, activity: [],
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
});
