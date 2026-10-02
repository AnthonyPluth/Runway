// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {}, connected: true }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const calls = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path);
const body = (c: unknown[]) => (c[1] as { body: Record<string, unknown> }).body;

const house = {
  id: 7, name: "House", kind: "home", value: 540000, as_of: "2026-09-01", source: "manual", yearly_change: 3, address: null, url: null,
  loan_account_id: "mort", auto_update: 0, low: null, high: null, last_lookup: null, next_lookup: null, current_value: 540000,
};
const car = { ...house, id: 8, name: "Outback", kind: "vehicle", value: 22500, current_value: 22500, loan_account_id: null, yearly_change: -12 };
const nw = (withAssets = true) => ({
  today: "2026-09-30", net: 400000, assets: 600000, liabilities: 200000, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
  assets_list: withAssets ? [house, car] : [], loan_accounts: [{ id: "mort", name: "Mortgage", kind: "loan" }], realie: { configured: false, used: 0, limit: 0 }, excluded: [],
  groups: [
    { key: "cash", label: "Cash", side: "asset", total: 5000, items: [{ type: "account", id: "chk", name: "Checking", org: "Chase", value: 5000, as_of: "2026-09-30" }] },
    { key: "equity", label: "Equity (vested)", side: "asset", total: 66000, items: [{ type: "equity", id: "c1", name: "Acme Robotics", value: 66000, as_of: "2026-09-01" }] },
    ...(withAssets ? [
      { key: "home", label: "Real estate", side: "asset", total: 540000, items: [{ type: "asset", id: 7, name: "House", value: 540000, as_of: "2026-09-01", source: "manual", equity: 340000, loan: { account_id: "mort", name: "Mortgage", owed: 200000 } }] },
      { key: "vehicle", label: "Vehicles", side: "asset", total: 22500, items: [{ type: "asset", id: 8, name: "Outback", value: 22500, as_of: "2026-09-01", source: "manual" }] },
    ] : []),
    { key: "loan", label: "Loans", side: "liability", total: 200000, items: [{ type: "account", id: "mort", name: "Mortgage", org: "Chase", value: 200000, as_of: "2026-09-30" }] },
  ],
});

let data = nw();
beforeEach(() => {
  data = nw();
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/networth" ? data : { ok: true })) as never);
});

describe("the breakdown", () => {
  it("lists each asset once, with no separate assets card", async () => {
    render(NetWorth);
    await screen.findByRole("button", { name: /^House, / });
    expect(screen.getAllByText("House")).toHaveLength(1);
    expect(screen.queryByText("Home, vehicles and other assets")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add an asset" })).not.toBeInTheDocument();
  });

  it("opens an asset row in a side panel with its actions, and removes it from there", async () => {
    render(NetWorth);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^House, / }));
    const dialog = await screen.findByRole("dialog", { name: "House" });
    expect(within(dialog).getByText(/\$340,000\.00 equity after Mortgage/)).toBeInTheDocument();
    // The kind shows once, in the header; the value is one editable field, not a figure plus an "Update value" button.
    expect(within(dialog).getAllByText(/Home \/ property/)).toHaveLength(1);
    expect(within(dialog).queryByText(/\$540,000/)).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText(/^Value/)).toHaveValue(540000);
    expect(within(dialog).queryByRole("button", { name: "Update value" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Edit details" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    const confirm = await screen.findByRole("dialog", { name: "Remove House?" });
    expect(confirm).toHaveTextContent("Its value history goes with it");
    expect(calls("/api/assets/7/remove")).toHaveLength(0);   // nothing happens until it's confirmed
    await user.click(within(confirm).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls("/api/assets/7/remove")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows a vehicle's value link and saves a new value from the panel", async () => {
    render(NetWorth);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Outback, / }));
    const dialog = await screen.findByRole("dialog", { name: "Outback" });
    expect(within(dialog).getByRole("link", { name: /Check on KBB/ })).toHaveAttribute("href", expect.stringContaining("kbb.com"));
    const input = within(dialog).getByLabelText(/^Value/);
    await user.clear(input);
    await user.type(input, "21000");
    await user.tab();
    await waitFor(() => expect(calls("/api/assets/8")).toHaveLength(1));
    expect(body(calls("/api/assets/8")[0])).toEqual({ value: "21000" });
  });

  it("has no hand-typed value for a home Realie values, only its lookup", async () => {
    data = nw();
    data.realie = { configured: true, used: 3, limit: 25 };
    data.assets_list = [{ ...house, address: "1 Main St, Springfield, IL 62701", source: "realie", realie_valued: true } as unknown as typeof house, car];
    render(NetWorth);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^House, / }));
    const dialog = await screen.findByRole("dialog", { name: "House" });
    expect(within(dialog).queryByLabelText(/^Value/)).not.toBeInTheDocument();
    expect(within(dialog).getByText("$540,000.00")).toBeInTheDocument();   // the figure, not a field
    expect(within(dialog).getByRole("button", { name: "Update from Realie" })).toBeInTheDocument();
  });

  it("edits details in the panel and goes back to the summary", async () => {
    render(NetWorth);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Outback, / }));
    const dialog = await screen.findByRole("dialog", { name: "Outback" });
    await user.click(within(dialog).getByRole("button", { name: "Edit details" }));
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Outback");
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(within(dialog).getByLabelText(/^Value/)).toBeInTheDocument();
  });

  it("adds to a group with its + Add, starting on that kind", async () => {
    render(NetWorth);
    const user = userEvent.setup();
    expect(screen.queryByRole("button", { name: "Add to Cash" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Add to Vehicles" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a vehicle" });
    expect(within(dialog).getByLabelText("What is it")).toHaveValue("vehicle");
    await user.type(within(dialog).getByLabelText("Name"), "Bike");
    await user.type(within(dialog).getByLabelText(/Value today/), "800");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls("/api/assets")).toHaveLength(1));
    expect(body(calls("/api/assets")[0])).toMatchObject({ name: "Bike", kind: "vehicle", value: "800" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(calls("/api/networth")).toHaveLength(2));
  });

  it("prompts to add an asset when there are none", async () => {
    data = nw(false);
    render(NetWorth);
    const user = userEvent.setup();
    expect(await screen.findByText(/Add a home, vehicle or other asset/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add an asset" }));
    expect(await screen.findByRole("dialog", { name: "Add a home" })).toBeInTheDocument();
  });

  it("folds a group away and back", async () => {
    render(NetWorth);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cash, collapse" }));
    expect(screen.queryByRole("button", { name: /^Checking, / })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cash, expand" }));
    expect(screen.getByRole("button", { name: /^Checking, / })).toBeInTheDocument();
  });

  it("links a company row under Equity (vested) to the equity view", async () => {
    render(NetWorth);
    expect(await screen.findByRole("link", { name: /Acme Robotics/ })).toHaveAttribute("href", "#networth/equity");
  });
});
