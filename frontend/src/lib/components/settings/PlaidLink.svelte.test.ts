// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({
  app: { state: { owners: [], primary_account: null, horizon_days: 90 }, version: 0 },
  reload: vi.fn(), refreshState: vi.fn(),
}));

import { api } from "$lib/api";
import { reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import type { PlaidItem, PlaidStatus, SettingsAccount } from "./types";
import { acct, card, ignored, item, matchCall, matched, serve, status, unmatched } from "../../../test/plaidSettings";
import AccountRow from "./AccountRow.svelte";
import AccountsSection from "./AccountsSection.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(reload).mockClear(); vi.mocked(toast.success).mockClear(); });

describe("Settings → Accounts: investment accounts from Plaid", () => {
  const roth = { id: "iv1", name: "Roth IRA", subtype: "ira", mask: "0001", balance: 5000, account_id: null };
  const cands = [{ id: "roth", name: "Roth", display_name: "My Roth", balance: 5000, linked_to: null }, { id: "taken", name: "Taken", balance: 10, linked_to: "iv9" }];
  const invItem = (accounts: PlaidItem["accounts"]) => item(accounts, { item_id: "it2", institution_name: "Fidelity", bank: false, products: ["investments"], candidates: cands });
  const inv = (over: Partial<SettingsAccount> = {}) => acct({ id: "roth", name: "Roth", display_name: "My Roth", kind: "investment", balance: 5000, ...over });

  it("lists an unmatched investment account in New from Plaid with the investment choices", async () => {
    serve(status([item([unmatched]), invItem([roth])]));
    render(AccountsSection, { accounts: [acct(), inv()] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    expect(within(group).getByText("Roth IRA")).toBeInTheDocument();
    const selects = within(group).getAllByRole("combobox");
    expect(selects).toHaveLength(2);
    expect(within(selects[1]).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Add as a new account", "Same as My Roth ($5,000.00)", "Don't count it"]);
  });

  it("matches it through POST /api/plaid/match with the investment toast", async () => {
    serve(status([invItem([roth])]));
    render(AccountsSection, { accounts: [inv()] });
    const group = await screen.findByRole("region", { name: "New from Plaid" });
    await userEvent.selectOptions(within(group).getByRole("combobox"), "roth");
    await waitFor(() => expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "iv1", target: "roth" } }));
    expect(toast.success).toHaveBeenCalledWith("Matched: it's counted once");
    vi.mocked(api).mockClear();
    await userEvent.selectOptions(within(group).getByRole("combobox"), "new");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "iv1", target: "new" } }));
    expect(toast.success).toHaveBeenCalledWith("Added to your accounts");
    vi.mocked(api).mockClear();
    await userEvent.selectOptions(within(group).getByRole("combobox"), "ignore");
    await waitFor(() => expect(matchCall()![1]).toMatchObject({ body: { plaid_account_id: "iv1", target: "ignore" } }));
    expect(toast.success).toHaveBeenCalledWith("Left out");
  });

  it("keeps an investment account you left out reachable, and doesn't list a decided one", async () => {
    serve(status([invItem([{ ...roth, account_id: "ignore" }, { ...roth, id: "iv2", name: "Brokerage", account_id: "roth" }])]));
    render(AccountsSection, { accounts: [inv()] });
    expect(await screen.findByText(/1 Plaid account you're not using/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New from Plaid" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("ignore");
  });

  it("shows an investment account's data source and unlinks it", async () => {
    const st = status([invItem([{ ...roth, account_id: "roth" }])]);
    render(AccountRow, { a: inv(), cash: [], byName: {}, plaid: st, mine: [] });
    expect(screen.getByText("SimpleFIN + Plaid ••0001")).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "Data source" });
    expect(within(section).getByText(/Linked to Fidelity Roth IRA ••0001/)).toBeInTheDocument();
    expect(within(section).getByText(/Plaid synced Sep 30/)).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "iv1", target: "" } }));
    expect(toast.success).toHaveBeenCalledWith("Unmatched");
  });

  it("links an unlinked investment account to a Plaid account at its institution", async () => {
    const st = status([invItem([roth, { ...roth, id: "iv3", name: "Other IRA", mask: "7", account_id: "elsewhere" }])]);
    render(AccountRow, { a: inv(), cash: [], byName: {}, plaid: st, mine: [] });
    const select = within(screen.getByRole("region", { name: "Data source" })).getByRole("combobox", { name: "Link to a Plaid account" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Choose…", "Fidelity Roth IRA ••0001 · $5,000.00", "Connect an investment account through Plaid…"]);
    await userEvent.selectOptions(select, "iv1");
    await waitFor(() => expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "iv1", target: "roth" } }));
    expect(toast.success).toHaveBeenCalledWith("Matched: it's counted once");
  });

  it("lets an investment account added from Plaid be changed to one of yours", async () => {
    const own = inv({ id: "pl:iv1", name: "Roth IRA", display_name: null, provider: "plaid" });
    const st = status([invItem([{ ...roth, account_id: "pl:iv1" }])]);
    render(AccountRow, { a: own, cash: [], byName: {}, plaid: st, mine: [] });
    expect(screen.getByText("Plaid ••0001")).toBeInTheDocument();
    const select = within(screen.getByRole("region", { name: "Data source" })).getByRole("combobox", { name: "Which of your accounts this is" });
    expect((select as HTMLSelectElement).value).toBe("new");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toContain("Its own account");
    await userEvent.selectOptions(select, "roth");
    await waitFor(() => expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "iv1", target: "roth" } }));
  });
});

describe("Settings → Accounts: linking from an account row", () => {
  const show = (a: SettingsAccount, st: PlaidStatus | null) => render(AccountRow, { a, cash: [acct()], byName: {}, plaid: st, mine: [acct(), card] });
  const st = status([item([unmatched, matched, ignored])]);

  it("links an unlinked account to one of the unmatched Plaid accounts", async () => {
    show(card, st);
    expect(screen.queryByText("not linked to Plaid")).toBeNull();
    expect(screen.getByText("no statement")).toBeInTheDocument();
    const select = screen.getByRole("combobox", { name: "Link to a Plaid account" });
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(expect.arrayContaining(["Choose…"]));
    expect(options.some((o) => o?.startsWith("Chase Freedom ••4242"))).toBe(true);
    expect(options.some((o) => o?.includes("Total Checking"))).toBe(false);
    expect(options).toContain("Connect a new bank through Plaid…");
    await userEvent.selectOptions(select, "pa1");
    await waitFor(() => expect(matchCall()).toBeTruthy());
    expect(matchCall()![1]).toEqual({ method: "POST", body: { plaid_account_id: "pa1", target: "amex" } });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("opens the row at its Statement from the Enter… action on the line", async () => {
    show(card, st);
    const details = document.querySelector("details")!;
    expect(details.open).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Enter…" }));
    await waitFor(() => expect(details.open).toBe(true));
    const section = screen.getByRole("region", { name: "Statement" });
    expect(document.activeElement).toBe(within(section).getByLabelText("Closing date"));
    expect(screen.getByRole("region", { name: "Data source" })).toBeInTheDocument();
  });

  it("says why a linked card's bank sends no statement, and offers to enter it", () => {
    const linked = { ...card, plaid_account_id: "pa1", plaid_link: { transactions: true, institution: "Chase", mask: "4242", closed: null,
      statement_note: "PRODUCTS_NOT_SUPPORTED" } };
    show(linked, st);
    expect(screen.getByText("no statement")).toHaveAttribute("title", "This bank doesn't share card statements through Plaid.");
    const section = screen.getByRole("region", { name: "Statement" });
    expect(within(section).getByText(/This bank doesn't share card statements through Plaid\. Enter the latest statement/)).toBeInTheDocument();
    expect(within(section).getByRole("form", { name: "Enter a statement" })).toBeInTheDocument();
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
