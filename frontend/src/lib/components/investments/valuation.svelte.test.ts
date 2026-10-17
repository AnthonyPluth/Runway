// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("./live", async (orig) => ({ ...(await orig<typeof import("./live")>()), livePrices: vi.fn(() => () => {}) }));

import { api } from "$lib/api";
import { holding } from "../../../test/fixtures";
import InvestmentsView from "./InvestmentsView.svelte";
import { livePrices } from "./live";
import { inv } from "./state.svelte";

const perf = { start: "2026-08-01", return: 0.1, benchmark_return: 0.08, gain: 100 };
const acct = (over: object) => ({ id: "a1", item_id: "i1", name: "Roth IRA", hidden: 0, hidden_in_accounts: 0, source: "plaid", institution_name: "Fidelity", tracked: 0, drift: null, balance: 2500, ...over });
const page = (accounts: object[], status: object = {}) => {
  const data = {
    today: "2026-09-30", total: 2500, unrealized_gain: 500, cost_basis: 2000, day_change: 12, day_change_pct: 0.005, cost_missing: 0, cost_missing_value: 0,
    holdings: [holding()], allocation: { asset_class: [], account: [], sector: [], holding: [] }, income: { months: [], income: [], fees: [], income_12m: 0, fees_12m: 0 },
    history: { dates: ["2026-08-01", "2026-09-29"], value: [2000, 2500], flows: [0, 0], invested: [2000, 2000], twr: [0, 0.1], benchmark: [0, 0.08], missing_prices: [], estimated_before: "2026-08-15" },
    performance: perf, periods: { "1M": perf }, xray: [], plan: {}, accounts,
  };
  const st = { inv_accounts: accounts.length, items: [], simplefin_seen: [], last_inv_sync: null, simplefin_last_sync: null, ...status };
  vi.mocked(api).mockImplementation((async (path: string) => (path.startsWith("/api/investments?") ? data : st)) as never);
};
const open = async () => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Valuation dates" }));
  return { user, sheet: await screen.findByRole("dialog", { name: "Valuation dates" }) };
};

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(livePrices).mockClear(); inv.period = "1Y"; });

describe("Investments: valuation dates", () => {
  it("lists the accounts from the accounts list, each dated by its own sync, and says when they differ", async () => {
    page([acct({}), acct({ id: "sf:vw", item_id: "sf", name: "Vestwell 401k", source: "simplefin", institution_name: "Vestwell", balance: 20000 })], {
      items: [{ item_id: "i1", sides: { investments: { last_sync: "2026-09-28T15:00:00Z", error: null } } }],
      simplefin_last_sync: "2026-09-30T15:00:00Z", last_inv_sync: "2026-09-28T15:00:00Z",
    });
    render(InvestmentsView);
    const { sheet } = await open();
    expect(within(sheet).getByTestId("valuation-headline")).toHaveTextContent("Values from different dates");
    const rows = within(sheet).getAllByTestId("valuation-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Plaid holdings sync");
    expect(rows[1]).toHaveTextContent("SimpleFIN sync");
    // the same accounts the Accounts card lists, and it is what "Show the accounts" goes to
    const card = screen.getByTestId("accounts-card");
    expect(card).toHaveAttribute("id", "inv-accounts");
    for (const n of ["Roth IRA", "Vestwell 401k"]) expect(within(card).getByText(n)).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Show the accounts" })).toBeInTheDocument();
  });

  it("says Date unavailable without sync metadata, keeps a hidden account apart and a duplicate counted once", async () => {
    page([acct({ also_simplefin: true }), acct({ id: "h", name: "Hidden IRA", hidden: 1, hidden_in_accounts: 1 })]);
    render(InvestmentsView);
    const { sheet } = await open();
    expect(within(sheet).getByTestId("valuation-headline")).toHaveTextContent("Date unavailable");
    const rows = within(sheet).getAllByTestId("valuation-row");
    expect(rows[0]).toHaveTextContent("Last synced: Date unavailable");
    expect(rows[0]).toHaveTextContent("Also sent by SimpleFIN; counted once");
    expect(within(sheet).getByTestId("valuation-left")).toHaveTextContent("Hidden IRA");
    expect(within(sheet).getByTestId("valuation-left")).toHaveTextContent("Left out in Settings → Accounts");
    expect(within(sheet).getAllByText("Hidden IRA")).toHaveLength(1);
    expect(within(sheet).getByText(/No live quotes are loaded/)).toBeInTheDocument();
  });

  it("shows hand-entered funds with their save date, or Date unavailable, apart from the holdings sync", async () => {
    page([acct({ id: "sf:vw", item_id: "sf", name: "Vestwell 401k", source: "simplefin", tracked: 2, tracked_updated: "2026-09-10" }),
      acct({ id: "sf:x", item_id: "sf", name: "Other 401k", source: "simplefin", tracked: 1 })], { simplefin_last_sync: "2026-09-30T15:00:00Z" });
    render(InvestmentsView);
    const { sheet } = await open();
    const [a, b] = within(sheet).getAllByTestId("valuation-row");
    expect(a).toHaveTextContent("Funds entered by hand, last saved: Sep 10, 2026");
    expect(b).toHaveTextContent("Funds entered by hand, last saved: Date unavailable");
  });

  it("separates live quote time from the holdings sync and the chart dates, leaving the page's own notice alone", async () => {
    page([acct({})], { items: [{ item_id: "i1", last_sync: "2026-09-28T15:00:00Z" }], last_inv_sync: "2026-09-28T15:00:00Z" });
    render(InvestmentsView);
    await screen.findByTestId("holdings-updated");
    const onQuotes = vi.mocked(livePrices).mock.calls[0][0];
    onQuotes({ quotes: {}, market: "closed", as_of: "2026-09-30T20:00:00Z" });
    const { sheet } = await open();
    expect(within(sheet).getByTestId("valuation-headline")).toHaveTextContent("Values from different dates");
    expect(within(sheet).getByTestId("valuation-groups")).toHaveTextContent(/Latest quotes, market closed/);
    expect(within(sheet).getByText(/The market is closed\. The total uses the latest quotes/)).toBeInTheDocument();
    expect(within(sheet).getByText(/The charts run to Sep 29, 2026 and are as of the last sync, not live quotes\. Values before Aug 15, 2026 are estimated/)).toBeInTheDocument();
    expect(screen.getByTestId("holdings-updated")).toHaveTextContent("Market closed");
  });

  it("opening it makes no request", async () => {
    page([acct({})]);
    render(InvestmentsView);
    await screen.findByRole("button", { name: "Valuation dates" });
    const before = vi.mocked(api).mock.calls.length;
    await open();
    expect(vi.mocked(api).mock.calls.length).toBe(before);
  });
});
