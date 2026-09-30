// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {} }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import NetWorth from "../../../pages/NetWorth.svelte";

const calls = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path);

const grant = {
  id: "g1", company_id: "c1", kind: "iso", label: "ES-1", granted_on: "2023-03-01", quantity: 40000, strike: 0.85, vest_start: "2023-03-01",
  vest_months: 48, cliff_months: 12, vest_every: 1, exercised: null, expires_on: "2033-03-01", vested: 20000, fully_vested_on: "2027-03-01",
  schedule: [], vested_value: 66000, unvested_value: 66000,
};
const equity = {
  companies: [{ id: "c1", name: "Acme Robotics", share_price: 4.2, price_as_of: "2026-09-01", in_networth: 1, source: "manual", grants: [grant], vested_value: 66000, unvested_value: 66000 }],
  vested_value: 66000, unvested_value: 66000, in_networth: 66000,
  carta: { env: "mock", client_id: null, has_secret: false, connected: true, last_sync: null, last_error: null, web_last: null, web_error: null, web_capture: false },
};
const nw = {
  today: "2026-09-30", net: 66000, assets: 66000, liabilities: 0, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
  assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded: [],
  groups: [{ key: "equity", label: "Equity (vested)", side: "asset", total: 66000, items: [{ type: "equity", id: "c1", name: "Acme Robotics", value: 66000, as_of: "2026-09-01" }] }],
};

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/networth") return nw;
    if (path === "/api/equity") return equity;
    return { ok: true };
  }) as never);
});

describe("Net worth summary", () => {
  it("has no Equity card, and its Equity (vested) group links to the equity view", async () => {
    render(NetWorth);
    const link = await screen.findByRole("link", { name: "Equity (vested)" });
    expect(link).toHaveAttribute("href", "#networth/equity");
    expect(screen.queryByRole("heading", { name: "Equity" })).not.toBeInTheDocument();
    expect(calls("/api/equity")).toHaveLength(0);
  });
});

describe("#networth/equity", () => {
  it("shows vested and still-to-vest figures, the companies and a way back", async () => {
    render(NetWorth, { sub: "equity" });
    expect(await screen.findByRole("heading", { name: "Equity" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Net worth" })).toHaveAttribute("href", "#networth");
    expect(screen.getByText("Vested now")).toBeInTheDocument();
    expect(screen.getAllByText("Still to vest").length).toBeGreaterThan(0);
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
    expect(screen.getByText("How is this valued?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync from Carta" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("edits a grant in a side panel, then saves and closes it", async () => {
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit ES-1" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit grant" });
    const shares = within(dialog).getByLabelText("Shares");
    expect(shares).toHaveValue(40000);
    await user.clear(shares);
    await user.type(shares, "50000");
    await user.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await waitFor(() => expect(calls("/api/equity/grants/g1")).toHaveLength(1));
    expect((calls("/api/equity/grants/g1")[0][1] as { body: Record<string, string> }).body).toMatchObject({ kind: "iso", quantity: "50000", strike: "0.85" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls("/api/equity")).toHaveLength(2);
  });

  it("adds a grant to a company from the same panel, and cancel leaves nothing behind", async () => {
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a grant" }));
    let dialog = await screen.findByRole("dialog", { name: "Add a grant" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls("/api/equity/companies/c1/grants")).toHaveLength(0);
    // Bits UI hands the page its pointer events back once the panel has finished closing.
    await waitFor(() => expect(document.body.style.pointerEvents).not.toBe("none"));

    await user.click(screen.getByRole("button", { name: "Add a grant" }));
    dialog = await screen.findByRole("dialog", { name: "Add a grant" });
    await user.type(within(dialog).getByLabelText("Shares"), "1000");
    await user.click(within(dialog).getByRole("button", { name: "Add grant" }));
    await waitFor(() => expect(calls("/api/equity/companies/c1/grants")).toHaveLength(1));
    expect((calls("/api/equity/companies/c1/grants")[0][1] as { body: Record<string, string> }).body).toMatchObject({ quantity: "1000", kind: "iso" });
  });

  it("still saves the in-net-worth toggle", async () => {
    render(NetWorth, { sub: "equity" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: /Count in net worth/ }));
    await waitFor(() => expect(calls("/api/equity/companies/c1")).toHaveLength(1));
    expect((calls("/api/equity/companies/c1")[0][1] as { body: unknown }).body).toEqual({ in_networth: false });
  });
});
