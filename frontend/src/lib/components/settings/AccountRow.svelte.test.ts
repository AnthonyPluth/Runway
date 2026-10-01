// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { owners: [], primary_account: null }, version: 0 }, reload: vi.fn(), refreshState: vi.fn() }));

import { api } from "$lib/api";
import { app, refreshState, reload } from "$lib/app.svelte";
import AccountRow from "./AccountRow.svelte";
import type { SettingsAccount } from "./types";

const acct = (over: Partial<SettingsAccount> = {}): SettingsAccount => ({ id: "sav", name: "Savings", kind: "savings", balance: 1000, networth_hidden: 0, ...over });
const show = (a: SettingsAccount) => render(AccountRow, { a, cash: [a], byName: {} });

beforeEach(() => {
  vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({ ok: true } as never);
  app.state = { connected: true, owners: [], primary_account: null };
});

describe("leaving an account out of net worth, from Settings", () => {
  it("tags an excluded account and shows its box unchecked", () => {
    show(acct({ networth_hidden: 1 }));
    expect(screen.getByText("not in net worth")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Count in net worth" })).not.toBeChecked();
  });

  it("has no tag on a counted account, and unticking the box saves networth_hidden: 1", async () => {
    show(acct());
    expect(screen.queryByText("not in net worth")).toBeNull();
    const box = screen.getByRole("checkbox", { name: "Count in net worth" });
    expect(box).toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(api).toHaveBeenCalled());
    const [path, opts] = vi.mocked(api).mock.calls[0] as [string, { method: string; body: Record<string, unknown> }];
    expect(path).toBe("/api/accounts/sav");
    expect(opts.body.networth_hidden).toBe(1);
  });
});

describe("choosing the forecast's account, from Settings", () => {
  const chk = acct({ id: "chk", name: "Checking", kind: "checking" });

  it("offers another cash account for the forecast, saving the same setting as Overview's picker", async () => {
    app.state = { connected: true, owners: [], primary_account: "chk" };
    render(AccountRow, { a: acct(), cash: [chk, acct()], byName: {} });
    expect(screen.queryByText("primary")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Use for the forecast" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { primary_account: "sav" } }));
    expect(refreshState).toHaveBeenCalled();
  });

  it("tags the account in use instead, including a lone checking account nobody chose", () => {
    render(AccountRow, { a: chk, cash: [chk, acct()], byName: {} });
    expect(screen.getByText("primary")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use for the forecast" })).toBeNull();
  });

  it("isn't offered on a card", () => {
    show(acct({ id: "cc", kind: "credit" }));
    expect(screen.queryByRole("button", { name: "Use for the forecast" })).toBeNull();
  });
});

describe("an account's logo, from Settings", () => {
  it("opens the logo picker for this account, and refreshes the state after a change", async () => {
    vi.mocked(api).mockResolvedValueOnce({ choice: null, searchable: false, configured: true, candidates: [], error: null } as never);
    show(acct({ display_name: "Rainy day" }));
    await userEvent.click(screen.getByRole("button", { name: "Logo for Rainy day" }));
    expect(api).toHaveBeenCalledWith("/api/accounts/sav/logo-options");
    await userEvent.type(await screen.findByRole("textbox", { name: "The website whose logo to use" }), "ally.com{Enter}");
    await waitFor(() => expect(api).toHaveBeenLastCalledWith("/api/accounts/sav/logo", { method: "POST", body: { website: "ally.com" } }));
    await waitFor(() => expect(refreshState).toHaveBeenCalled());
  });
});

describe("a card's statement, from Settings", () => {
  const cc = (over: Partial<SettingsAccount> = {}) => acct({ id: "cc", name: "Visa", kind: "credit", balance: -400, pay_from: "chk", ...over });
  const open = () => userEvent.click(document.querySelector("summary")!);

  it("shows Plaid's statement read only, with nothing more on the line", async () => {
    show(cc({ plaid_account_id: "p1", plaid_link: { institution: "Chase", closed: true },
      statement: { source: "plaid", institution: "Chase", closed: "2026-09-10", due: "2026-10-05", balance: 812.4, minimum: 35 } }));
    expect(screen.queryByText("no statement")).toBeNull();
    expect(screen.queryByText(/statement due/)).toBeNull();
    await open();
    const section = screen.getByRole("region", { name: "Statement" });
    expect(section).toHaveTextContent("$812.40 · closed Sep 10 · due Oct 5 · min $35.00");
    expect(section).toHaveTextContent("From Chase via Plaid");
    expect(within(section).queryByRole("form")).toBeNull();
  });

  it("asks for a statement when there's none, and saves the one entered", async () => {
    show(cc({ statement: null, statements: [] }));
    expect(screen.getByText("no statement")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Enter…" }));
    const form = within(screen.getByRole("region", { name: "Statement" })).getByRole("form", { name: "Enter a statement" });
    await userEvent.type(within(form).getByLabelText("Closing date"), "2026-09-10");
    await userEvent.type(within(form).getByLabelText("Statement balance"), "812.40");
    await userEvent.type(within(form).getByLabelText("Due date"), "2026-10-05");
    await userEvent.click(within(form).getByRole("button", { name: "Save statement" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/cc/statements", { method: "POST",
      body: { statement_date: "2026-09-10", balance: 812.4, due_date: "2026-10-05", minimum_payment: "" } }));
    expect(reload).toHaveBeenCalled();
  });

  it("shows the latest one entered, its due date on the line, and the earlier ones, each deletable", async () => {
    show(cc({ statement: { source: "manual", closed: "2026-09-10", due: "2026-10-21", balance: 500, minimum: null, stale: false },
      statements: [{ statement_date: "2026-09-10", balance: 500, due_date: "2026-10-21" },
        { statement_date: "2026-08-10", balance: 300, due_date: "2026-09-21", minimum_payment: 25 }] }));
    expect(screen.getByText("statement due Oct 21")).toBeInTheDocument();
    await open();
    const section = screen.getByRole("region", { name: "Statement" });
    expect(section).toHaveTextContent("$500.00 · closed Sep 10 · due Oct 21");
    expect(section).toHaveTextContent("Entered by you");
    expect(within(section).queryByRole("form")).toBeNull();     // behind "Enter the next statement"
    await userEvent.click(within(section).getByRole("button", { name: "Enter the next statement" }));
    expect(within(section).getByRole("form", { name: "Enter a statement" })).toBeInTheDocument();
    const earlier = within(section).getByRole("list", { name: "Earlier statements" });
    expect(earlier).toHaveTextContent("$300.00 · closed Aug 10 · due Sep 21 · min $25.00");
    await userEvent.click(within(earlier).getByRole("button", { name: /^Delete the statement that closed Aug.10$/ }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/cc/statements/2026-08-10/remove", { method: "POST" }));
  });

  it("flags one that's out of date", () => {
    show(cc({ statement: { source: "manual", closed: "2026-07-10", due: "2026-08-05", balance: 500, stale: true, next_close: "2026-08-10" },
      statements: [{ statement_date: "2026-07-10", balance: 500, due_date: "2026-08-05" }] }));
    expect(screen.getByText("statement out of date")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enter…" })).toBeInTheDocument();
  });

  it("opens at its Statement from a link to #setup/accounts?account=<id>", async () => {
    show(cc({ id: "pl:a|b", statement: null, statements: [] }));
    const details = document.querySelector("details")!;
    expect(details.open).toBe(false);
    location.hash = "#setup/accounts?account=" + encodeURIComponent("pl:a|b");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await waitFor(() => expect(details.open).toBe(true));
    expect(location.hash).toBe("#setup/accounts");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Closing date")));
  });
});

describe("deleting an account, from Settings", () => {
  it("says what goes with it, offers a backup, and deletes it", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => path.endsWith("/removal")
      ? { name: "Savings", transactions: 120, recurring: 2, rules: 1, statements: 0, holdings: 0, plaid: true } as never : { ok: true } as never);
    show(acct());
    await userEvent.click(screen.getByText("Savings"));
    await userEvent.click(screen.getByRole("button", { name: "Delete account…" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Savings?" });
    await waitFor(() => expect(dialog).toHaveTextContent("120 transactions, with their categories and splits"));
    expect(dialog).toHaveTextContent("2 recurring items on this account");
    expect(dialog).toHaveTextContent("1 rule that only applies to it");
    expect(dialog).toHaveTextContent("SimpleFIN and Plaid leave it out until you restore it");
    expect(within(dialog).getByRole("link", { name: "Download a backup first" })).toHaveAttribute("href", "#setup/advanced");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/sav/remove", { method: "POST" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(reload).toHaveBeenCalled();
  });
});
