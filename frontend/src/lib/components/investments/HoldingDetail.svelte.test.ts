// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig<typeof import("$lib/api")>()), api: vi.fn(), newPage: vi.fn() }));
// Live prices: the test hands the page a quote when it wants one.
let onQuotes: ((l: { quotes: Record<string, unknown>; market: string; as_of: string }) => void) | null = null;
vi.mock("./live", async (orig) => ({
  ...(await orig<typeof import("./live")>()),
  livePrices: vi.fn((cb) => { onQuotes = cb; return () => { onQuotes = null; }; }),
}));

import { ApiError, api } from "$lib/api";
import { holding } from "../../../test/fixtures";
import HoldingDetail from "./HoldingDetail.svelte";

const lot = (extra = {}) => ({ account_id: "a1", security_id: "s1", account_name: "Brokerage", quantity: 10, value: 2500, cost_basis: 2000,
  reported_cost_basis: 2000, per_share: 200, manual: false, ...extra });
const reply = (h = {}, extra = {}) => ({
  holding: holding({ lots: [lot(), lot({ account_id: "a2", account_name: "Roth IRA", quantity: 5, value: 1250, cost_basis: 1250, per_share: 250 })],
    quantity: 15, value: 3750, price: 250, cost_basis: 3250, gain: 500, gain_pct: 500 / 3250, allocation: 0.5, accounts: ["Brokerage", "Roth IRA"], ...h }),
  total: 7500, period: "1Y",
  history: { dates: ["2026-01-02", "2026-01-03", "2026-01-04"], prices: [200, 220, 250], start_price: 200, change: 50, change_pct: 0.25 },
  dividends_12m: 0, ...extra,
});

beforeEach(() => { vi.mocked(api).mockReset(); onQuotes = null; window.scrollTo = vi.fn(); });

describe("HoldingDetail", () => {
  it("shows the ticker, name, price and today's change, then the position and the accounts", async () => {
    vi.mocked(api).mockResolvedValue(reply());
    render(HoldingDetail, { id: "s1" });
    expect(await screen.findByRole("heading", { level: 1, name: "VTI" })).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/api/investments/holding/s1?period=1Y");
    expect(screen.getByText("Vanguard Total Market")).toBeInTheDocument();
    expect(screen.getByTestId("holding-price")).toHaveTextContent("$250.00");
    expect(screen.getByTestId("holding-today")).toHaveTextContent("▲ +$12.00 (+0.50%) today");
    const position = within(screen.getByTestId("holding-position"));
    expect(position.getByText("Market value").parentElement).toHaveTextContent("$3,750.00");
    expect(position.getByText("Share of portfolio").parentElement).toHaveTextContent("50%");
    expect(position.getByText("Shares").parentElement).toHaveTextContent("15");
    expect(position.getByText("Average cost").parentElement).toHaveTextContent("$216.67");
    expect(position.getByText("Total gain").parentElement).toHaveTextContent("+$500.00 (+15.4%)");
    const rows = within(screen.getByTestId("holding-accounts")).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Brokerage");
    expect(rows[0]).toHaveTextContent("10 shares · avg $200.00");
    expect(rows[0]).toHaveTextContent("$2,500.00");
    expect(rows[0]).toHaveTextContent("+25.0%");
    expect(rows[1]).toHaveTextContent("Roth IRA");
    expect(screen.getByTestId("holding-since")).toHaveTextContent("$200.00 on Jan 2, 2026 · +$50.00 (+25.0%) since");
    expect(screen.queryByText("Dividends")).not.toBeInTheDocument();
  });

  it("says 'This holding isn't in your portfolio any more' for an unknown id, with a link back", async () => {
    vi.mocked(api).mockRejectedValue(new ApiError("That holding isn't in your portfolio", 404));
    render(HoldingDetail, { id: "gone" });
    expect(await screen.findByText("This holding isn't in your portfolio any more")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Investments" })).toHaveAttribute("href", "#investments");
  });

  it("goes back to Investments from the back button", async () => {
    vi.mocked(api).mockResolvedValue(reply());
    render(HoldingDetail, { id: "s1" });
    await screen.findByTestId("holding-header");
    expect(screen.getByRole("link", { name: /Investments/ })).toHaveAttribute("href", "#investments");
  });

  it("offers to retry when the page can't be loaded for another reason", async () => {
    vi.mocked(api).mockRejectedValueOnce(new ApiError("Request failed (500)", 500)).mockResolvedValue(reply());
    render(HoldingDetail, { id: "s1" });
    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("holding-header")).toBeInTheDocument();
  });

  it("loads another period from the selector", async () => {
    vi.mocked(api).mockResolvedValue(reply());
    render(HoldingDetail, { id: "s1" });
    await screen.findByTestId("holding-header");
    await userEvent.click(screen.getByRole("radio", { name: "3M" }));
    await waitFor(() => expect(api).toHaveBeenLastCalledWith("/api/investments/holding/s1?period=3M"));
    expect(screen.getAllByRole("radio").map((r) => r.textContent?.trim())).toEqual(["1M", "3M", "YTD", "1Y", "2Y", "All"]);
  });

  it("says 'No cost basis' with the edit action when it's unknown, and edits it in place", async () => {
    vi.mocked(api).mockResolvedValue(reply({ gain: null, gain_pct: null, cost_known: false, lots: [lot({ cost_basis: null, per_share: null })] }));
    render(HoldingDetail, { id: "s1" });
    const edit = await screen.findByRole("button", { name: /No cost basis/ });
    expect(within(screen.getByTestId("holding-position")).getByText("Total gain").parentElement).toHaveTextContent("—");
    await userEvent.click(edit);
    expect(screen.getByLabelText("Price per share in Brokerage")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByLabelText("Price per share in Brokerage")).not.toBeInTheDocument();
  });

  it("keeps the period picked last when an older request finishes after it", async () => {
    let slow!: (r: unknown) => void;
    vi.mocked(api).mockImplementation(((path: string) =>
      path.endsWith("period=3M") ? new Promise((done) => { slow = done; }) : Promise.resolve(reply({}, { period: path.slice(-2) }))) as never);
    render(HoldingDetail, { id: "s1" });
    await screen.findByTestId("holding-header");
    await userEvent.click(screen.getByRole("radio", { name: "3M" }));   // slow
    await userEvent.click(screen.getByRole("radio", { name: "2Y" }));   // answers at once
    await waitFor(() => expect(screen.getByRole("radio", { name: "2Y" })).toBeChecked());
    slow(reply({ price: 111 }, { period: "3M" }));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByRole("radio", { name: "2Y" })).toBeChecked();
    expect(screen.getByTestId("holding-price")).toHaveTextContent("$250.00");
  });

  it("shows the dividends of the last 12 months when there were any", async () => {
    vi.mocked(api).mockResolvedValue(reply({}, { dividends_12m: 20 }));
    render(HoldingDetail, { id: "s1" });
    expect(await screen.findByText("$20.00 in the last 12 months")).toBeInTheDocument();
  });

  it("says so when no prices are kept for the period", async () => {
    vi.mocked(api).mockResolvedValue(reply({}, { history: { dates: [], prices: [], start_price: null, change: null, change_pct: null } }));
    render(HoldingDetail, { id: "s1" });
    expect(await screen.findByText("No price history for this period")).toBeInTheDocument();
    expect(screen.queryByTestId("holding-since")).not.toBeInTheDocument();
  });

  it("re-prices the price, today and the position while live prices come in", async () => {
    vi.mocked(api).mockResolvedValue(reply());
    render(HoldingDetail, { id: "s1" });
    await screen.findByTestId("holding-header");
    await waitFor(() => expect(onQuotes).not.toBeNull());
    onQuotes!({ quotes: { VTI: { price: 260, prev_close: 248, time: Date.now() / 1000, type: "ETF" } }, market: "open", as_of: "" });
    await waitFor(() => expect(screen.getByTestId("holding-price")).toHaveTextContent("$260.00"));
    expect(screen.getByTestId("holding-today")).toHaveTextContent("▲ +$180.00 (+4.84%) today");   // 15 shares x $12
    const position = within(screen.getByTestId("holding-position"));
    expect(position.getByText("Market value").parentElement).toHaveTextContent("$3,900.00");
    expect(position.getByText("Total gain").parentElement).toHaveTextContent("+$650.00");
    expect(position.getByText("Share of portfolio").parentElement).toHaveTextContent("51%");   // 3,900 of 7,650
    expect(within(screen.getByTestId("holding-accounts")).getAllByRole("listitem")[0]).toHaveTextContent("$2,600.00");
    expect(screen.getByTestId("holding-since")).toHaveTextContent("+$60.00 (+30.0%) since");
  });
});
