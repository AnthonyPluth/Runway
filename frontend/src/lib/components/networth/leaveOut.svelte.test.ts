// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { brands: {} }, version: 0 }, refreshState: vi.fn(), reload: vi.fn() }));

import { api } from "$lib/api";
import AccountsTable from "../investments/AccountsTable.svelte";
import NetWorth from "../../../pages/NetWorth.svelte";
import type { InvAccount } from "../investments/types";

const calls = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path);

const nw = (excluded: unknown[] = []) => ({
  today: "2026-09-30", net: 5000, assets: 5000, liabilities: 0, history: [], first_snapshot: null, change: { "30d": null, "90d": null, "1y": null },
  assets_list: [], loan_accounts: [], realie: { configured: false, used: 0, limit: 0 }, excluded,
  groups: [{ key: "cash", label: "Cash", side: "asset", total: 5000, items: [{ type: "account", id: "chk", name: "Checking", org: "Chase", value: 5000, as_of: "2026-09-30" }] }],
});

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("leaving an account out of net worth", () => {
  it("posts networth_hidden and reloads, and lists what's left out with a way back", async () => {
    let left = false;
    vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string; body?: Record<string, unknown> }) => {
      if (path === "/api/networth") return nw(left ? [{ id: "chk", name: "Checking", org: "Chase", kind: "checking" }] : []);
      if (path.startsWith("/api/accounts/")) { left = opts?.body?.networth_hidden === 1; return { ok: true }; }
      return {};
    }) as never);
    render(NetWorth);
    await userEvent.click(await screen.findByRole("button", { name: "Leave Checking out of net worth" }));
    await waitFor(() => expect(calls("/api/accounts/chk")).toHaveLength(1));
    expect((calls("/api/accounts/chk")[0][1] as { body: unknown }).body).toEqual({ networth_hidden: 1 });
    expect(await screen.findByText("Left out of net worth")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Count Checking in net worth again" }));
    await waitFor(() => expect(calls("/api/accounts/chk")).toHaveLength(2));
    expect((calls("/api/accounts/chk")[1][1] as { body: unknown }).body).toEqual({ networth_hidden: 0 });
    await waitFor(() => expect(screen.queryByText("Left out of net worth")).not.toBeInTheDocument());
  });
});

describe("the investment accounts", () => {
  const acct = (over: Partial<InvAccount>): InvAccount => ({ id: "a1", item_id: "i", name: "Roth IRA", official_name: null, subtype: null, mask: "3639", balance: 100, hidden: 0,
    hidden_in_accounts: 0, source: "plaid", institution_name: "Wealthfront", tracked: 0, drift: null, ...over });

  it("has no box to leave an account out, only a way to show one that was hidden", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true } as never);
    const onchanged = vi.fn();
    render(AccountsTable, { accounts: [acct({}), acct({ id: "a2", name: "Old", hidden: 1 }), acct({ id: "a3", name: "Dup", hidden: 1, duplicate_of: "a1" })], seen: [], onchanged });
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show Roth IRA again/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show Dup again/ })).not.toBeInTheDocument();   // a duplicate stays out (it would count twice)
    await userEvent.click(screen.getByRole("button", { name: "Show Old again" }));
    await waitFor(() => expect(calls("/api/plaid/accounts/a2")).toHaveLength(1));
    expect((calls("/api/plaid/accounts/a2")[0][1] as { body: unknown }).body).toEqual({ hidden: false });
    expect(onchanged).toHaveBeenCalled();
  });
});
