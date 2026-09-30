// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({
  app: { state: { owners: [], primary_account: null, horizon_days: 90, plaid_undecided: 1 }, version: 0 },
  reload: vi.fn(), refreshState: vi.fn(),
}));

import { api } from "$lib/api";
import { reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import AccountRow from "./AccountRow.svelte";
import AccountsSection from "./AccountsSection.svelte";
import PlaidItemRow from "./PlaidItemRow.svelte";
import type { PlaidItem, PlaidStatus, SettingsAccount } from "./types";

const acct = (over: Partial<SettingsAccount> = {}): SettingsAccount => ({ id: "chk", name: "Checking", kind: "checking", balance: 1000, ...over });
const card = acct({ id: "amex", name: "Amex Gold", kind: "credit", balance: -400, plaid_account_id: null, plaid_link: null });
const item = (accounts: PlaidItem["accounts"], over: Partial<PlaidItem> = {}): PlaidItem => ({
  item_id: "it1", institution_name: "Chase", products: ["transactions"], bank: true, last_sync: "2026-09-30 15:00:00", accounts, ...over });
const status = (items: PlaidItem[], over: Partial<PlaidStatus> = {}): PlaidStatus => ({ configured: true, env: "sandbox", client_id: "x", items, ...over });
const unmatched = { id: "pa1", name: "Freedom", subtype: "credit card", type: "credit", mask: "4242", balance: -120, ignored: 0, account_id: null };
const matched = { id: "pa2", name: "Total Checking", type: "depository", mask: "1111", balance: 900, ignored: 0, account_id: "chk" };
const ignored = { id: "pa3", name: "Old Savings", type: "depository", mask: "9999", balance: 5, ignored: 1, account_id: null };

const serve = (st: PlaidStatus | Error) => vi.mocked(api).mockImplementation(async (path: string) => {
  if (path === "/api/plaid/status") { if (st instanceof Error) throw st; return st; }
  return { ok: true };
});
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(toast.success).mockClear(); });
const matchCall = () => vi.mocked(api).mock.calls.find(([p]) => p === "/api/plaid/match");

describe("Settings → Accounts: New from Plaid", () => {
  it("lists Plaid accounts nobody has decided about at the top, with the three choices", async () => {
    serve(status([item([unmatched, matched])]));
    render(AccountsSection, { accounts: [acct({ plaid_account_id: "pa2" }), card] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    expect(within(group).getByText("Freedom")).toBeInTheDocument();
    expect(within(group).queryByText("Total Checking")).toBeNull();   // already matched: it's in its type group instead
    const options = within(within(group).getByRole("combobox")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Choose…", "Add as its own account", "Same as Amex Gold", "Don't use"]);
  });

  it("has no group when there's nothing to decide, or no Plaid", async () => {
    serve(status([item([matched])]));
    const { unmount } = render(AccountsSection, { accounts: [acct()] });
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/plaid/status"));
    expect(screen.queryByRole("region", { name: "New from Plaid" })).toBeNull();
    unmount();
    serve(new Error("nope"));
    render(AccountsSection, { accounts: [acct()] });
    expect(screen.queryByRole("region", { name: "New from Plaid" })).toBeNull();
  });

  it("adds one as its own account through POST /api/plaid/match, and says so", async () => {
    serve(status([item([unmatched])]));
    render(AccountsSection, { accounts: [acct()] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    await userEvent.selectOptions(within(group).getByRole("combobox"), "new");
    await waitFor(() => expect(matchCall()).toBeTruthy());
    expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "pa1", target: "new" } });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added to your accounts"));
    expect(reload).toHaveBeenCalled();
  });

  it("matches one to an account you already have, or leaves it out", async () => {
    serve(status([item([unmatched])]));
    render(AccountsSection, { accounts: [acct(), card] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    await userEvent.selectOptions(within(group).getByRole("combobox"), "amex");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "pa1", target: "amex" } }));
    expect(toast.success).toHaveBeenCalledWith("Matched. Choose where its data comes from under Accounts.");
    vi.mocked(api).mockClear();
    await userEvent.selectOptions(within(group).getByRole("combobox"), "ignore");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "pa1", target: "ignore" } }));
    expect(toast.success).toHaveBeenCalledWith("Left out");
  });

  it("keeps the ones you left out reachable, and only warns about investments still waiting", async () => {
    serve(status([item([unmatched, ignored])]));
    render(AccountsSection, { accounts: [acct()] });
    expect(await screen.findByText("1 Plaid account you're not using")).toBeInTheDocument();
    expect(screen.getByText("Old Savings")).toBeInTheDocument();
    // plaid_undecided is 1 and that one is the card above, so no investment warning
    expect(screen.queryByText(/investment account/)).toBeNull();
  });
});

describe("Settings → Accounts: linking from an account row", () => {
  const show = (a: SettingsAccount, st: PlaidStatus | null) => render(AccountRow, { a, cash: [acct()], byName: {}, plaid: st, mine: [acct(), card] });
  const st = status([item([unmatched, matched, ignored])]);

  it("links an unlinked account to one of the unmatched Plaid accounts", async () => {
    show(card, st);
    expect(screen.getByText("not linked to Plaid")).toBeInTheDocument();
    const select = screen.getByRole("combobox", { name: "Link to a Plaid account" });
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(expect.arrayContaining(["Choose…"]));
    expect(options.some((o) => o?.startsWith("Chase Freedom ••4242"))).toBe(true);
    expect(options.some((o) => o?.includes("Total Checking"))).toBe(false);   // already linked to another account
    expect(options).toContain("Connect a new bank through Plaid…");
    await userEvent.selectOptions(select, "pa1");
    await waitFor(() => expect(matchCall()).toBeTruthy());
    expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "pa1", target: "amex" } });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("opens the row at Data source from the Link… action on the line", async () => {
    show(card, st);
    const details = document.querySelector("details")!;
    expect(details.open).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Link…" }));
    await waitFor(() => expect(details.open).toBe(true));
    expect(screen.getByRole("region", { name: "Data source" })).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Link to a Plaid account" }));
  });

  it("shows a linked account's sources and unlinks it with target \"\"", async () => {
    const linked = acct({ plaid_account_id: "pa2", provider: "simplefin", plaid_link: { transactions: true, institution: "Chase", mask: "1111" } });
    show(linked, st);
    expect(screen.getByText("SimpleFIN + Plaid ••1111")).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "Data source" });
    expect(within(section).getByText(/Plaid synced Sep 30/)).toBeInTheDocument();
    expect(within(section).getByRole("combobox", { name: "Transactions from" })).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(matchCall()).toBeTruthy());
    expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "pa2", target: "" } });
    expect(toast.success).toHaveBeenCalledWith("Unmatched");
  });

  it("says SimpleFIN for an account with no Plaid, and hides Data source until Plaid is set up", () => {
    show(acct(), null);
    expect(screen.getByText("SimpleFIN")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Data source" })).toBeNull();
  });

  it("lets an account added from Plaid be changed to another account or left out", async () => {
    const own = acct({ id: "pl:pa1", name: "Freedom", kind: "credit", plaid_account_id: "pa1", provider: "plaid", plaid_link: { transactions: true, institution: "Chase", mask: "4242" } });
    const owned = { ...unmatched, account_id: "pl:pa1" };
    show(own, status([item([owned])]));
    expect(screen.getByText("Plaid ••4242")).toBeInTheDocument();
    const select = within(screen.getByRole("region", { name: "Data source" })).getByRole("combobox", { name: "Which of your accounts this is" });
    expect((select as HTMLSelectElement).value).toBe("pl:pa1");
    await userEvent.selectOptions(select, "ignore");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "pa1", target: "ignore" } }));
  });
});

describe("Settings → Bank connections: a Plaid connection", () => {
  const items = (it: PlaidItem) => render(PlaidItemRow, { it, items: [it] });

  it("has no per-account selects, just a count and a way to Accounts", () => {
    items(item([unmatched, matched, ignored]));
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText(/3 accounts/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1 needs a decision →" })).toHaveAttribute("href", "#setup/accounts");
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("points at Accounts when everything is decided", () => {
    items(item([matched]));
    expect(screen.getByText(/1 account/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage in Accounts" })).toHaveAttribute("href", "#setup/accounts");
  });

  it("still matches an investment connection's accounts here", async () => {
    const inv = item([{ id: "iv1", name: "Roth IRA", subtype: "ira", mask: "0001", balance: 5000, account_id: null }],
      { bank: false, products: ["investments"], candidates: [{ id: "roth", name: "Roth", balance: 5000, linked_to: null }] });
    items(inv);
    await userEvent.selectOptions(screen.getByRole("combobox"), "roth");
    await waitFor(() => expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "iv1", target: "roth" } }));
    expect(toast.success).toHaveBeenCalledWith("Matched: it's counted once");
  });
});
