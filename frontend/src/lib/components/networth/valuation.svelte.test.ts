// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {}, connected: true }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const acct = (id: string, name: string, value: number, as_of: string | null, extra = {}) => ({ type: "account", id, name, org: "Chase", value, as_of, ...extra });
const nw = (over: object = {}) => ({
  today: "2026-09-30", net: 400000, assets: 600000, liabilities: 200000, change: { "30d": null, "90d": null, "1y": null },
  history: [{ date: "2026-09-29", net: 399000, assets: 1, liabilities: 1 }, { date: "2026-09-30", net: 400000, assets: 1, liabilities: 1 }], first_snapshot: "2026-09-29",
  assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 },
  excluded: [{ id: "old", name: "Old brokerage", org: "Fidelity", kind: "investment", balance: 9000, as_of: "2026-01-02" }],
  groups: [
    { key: "cash", label: "Cash", side: "asset", total: 5000, items: [acct("chk", "Checking", 5000, "2026-09-30"), acct("sav", "Savings", 100, null)] },
    { key: "home", label: "Real estate", side: "asset", total: 540000, items: [{ type: "asset", id: 7, name: "House", value: 540000, as_of: "2026-09-01", source: "realie", equity: 340000, loan: { account_id: "mort", name: "Mortgage", owed: 200000 } }] },
    { key: "loan", label: "Loans", side: "liability", total: 200000, items: [acct("mort", "Mortgage", 200000, "2026-09-30")] },
  ],
  ...over,
});

let data = nw();
beforeEach(() => {
  data = nw();
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/networth" ? data : { ok: true })) as never);
});

const open = async () => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Valuation dates" }));
  return { user, sheet: await screen.findByRole("dialog", { name: "Valuation dates" }) };
};

describe("Net worth: valuation dates", () => {
  it("says the values are from different dates and lists them, with each contributor's source and date", async () => {
    render(NetWorth);
    const { sheet } = await open();
    expect(within(sheet).getByTestId("valuation-headline")).toHaveTextContent("Values from different dates");
    const groups = within(sheet).getByTestId("valuation-groups");
    expect(groups).toHaveTextContent("Sep 30, 2026 · Checking, Mortgage");
    expect(groups).toHaveTextContent("Sep 1, 2026 · House");
    expect(groups).toHaveTextContent("Date unavailable · Savings");
    const rows = within(sheet).getAllByTestId("valuation-row");
    expect(rows).toHaveLength(5);
    expect(rows[0]).toHaveTextContent("Checking");
    expect(rows[0]).toHaveTextContent("Chase · bank balance");
    expect(rows[0]).toHaveTextContent("Balance synced: Sep 30, 2026");
    expect(rows[1]).toHaveTextContent("Balance synced: Date unavailable");   // missing metadata is never presented as current
    expect(rows[2]).toHaveTextContent("Realie estimate");
    expect(rows[2]).toHaveTextContent("Value as of: Sep 1, 2026");
    expect(rows[2]).toHaveTextContent("Mortgage is counted once, under Liabilities");
    expect(within(sheet).getByText(/not today's valuations/)).toBeInTheDocument();
  });

  it("keeps an excluded account apart: not in the dates, under its own heading, listed once", async () => {
    render(NetWorth);
    const { sheet } = await open();
    const left = within(sheet).getByTestId("valuation-left");
    expect(left).toHaveTextContent("Old brokerage");
    expect(left).toHaveTextContent("Balance synced: Jan 2, 2026");
    expect(within(sheet).getByTestId("valuation-groups")).not.toHaveTextContent("Jan 2, 2026");
    expect(within(sheet).getAllByText("Old brokerage")).toHaveLength(1);
  });

  it("with one date for everything says so, and with only unknown dates says Date unavailable", async () => {
    data = nw({ excluded: [], groups: [{ key: "cash", label: "Cash", side: "asset", total: 5, items: [acct("chk", "Checking", 5, "2026-09-30")] }] });
    const { unmount } = render(NetWorth);
    let { sheet } = await open();
    expect(within(sheet).getByTestId("valuation-headline")).toHaveTextContent("All values are from Sep 30, 2026");
    expect(within(sheet).queryByTestId("valuation-groups")).toBeNull();
    unmount();
    data = nw({ excluded: [], groups: [{ key: "cash", label: "Cash", side: "asset", total: 5, items: [acct("chk", "Checking", 5, null)] }] });
    render(NetWorth);
    ({ sheet } = await open());
    expect(within(sheet).getByTestId("valuation-headline")).toHaveTextContent("Date unavailable");
  });

  it("sends nothing to open, reuses the breakdown as the list, and 'Show' goes to it", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(NetWorth);
    await screen.findByRole("button", { name: "Valuation dates" });
    const before = vi.mocked(api).mock.calls.length;
    const { user, sheet } = await open();
    expect(vi.mocked(api).mock.calls.length).toBe(before);
    expect(document.getElementById("nw-breakdown")).toHaveTextContent("What makes it up");
    await user.click(within(sheet).getByRole("button", { name: "Show what makes it up" }));
    await vi.waitFor(() => expect(scroll).toHaveBeenCalled());
    expect(scroll.mock.contexts[0]).toBe(document.getElementById("nw-breakdown"));
    expect(screen.queryByRole("dialog", { name: "Valuation dates" })).toBeNull();
  });
});
