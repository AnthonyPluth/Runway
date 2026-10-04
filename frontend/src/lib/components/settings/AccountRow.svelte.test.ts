// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { owners: [], primary_account: null }, version: 0 }, reload: vi.fn(), refreshState: vi.fn() }));

import { api } from "$lib/api";
import { app, refreshState, reload } from "$lib/app.svelte";
import { toast } from "svelte-sonner";
import AccountRow from "./AccountRow.svelte";
import type { SettingsAccount } from "./types";

const acct = (over: Partial<SettingsAccount> = {}): SettingsAccount => ({ id: "sav", name: "Savings", kind: "savings", balance: 1000, networth_hidden: 0, ...over });
const show = (a: SettingsAccount) => render(AccountRow, { a, cash: [a], byName: {} });

// What a field shows (the commas helper's `value` leaves its commas out).
const shown = (el: HTMLElement) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.get!.call(el);
// The Undo of the last toast that offered one.
const undoOf = () => {
  const call = vi.mocked(toast).mock.calls.findLast(([, o]) => (o as { action?: unknown })?.action);
  return (call![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick;
};

beforeEach(() => {
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear(); vi.mocked(reload).mockClear();
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

  it("offers another cash account for the forecast among its actions, saving the same setting as Overview's picker", async () => {
    app.state = { connected: true, owners: [], primary_account: "chk" };
    render(AccountRow, { a: acct(), cash: [chk, acct()], byName: {} });
    expect(screen.queryByText("Forecast")).toBeNull();
    const button = within(screen.getByRole("group", { name: "Account actions" })).getByRole("button", { name: "Use for the forecast" });
    expect(button.closest("summary")).toBeNull();   // in the opened row, not on its line
    await userEvent.click(button);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { primary_account: "sav" } }));
    expect(refreshState).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith("Savings is now the forecast account");
  });

  it("tags the account in use instead, including a lone checking account nobody chose", () => {
    render(AccountRow, { a: chk, cash: [chk, acct()], byName: {} });
    expect(within(document.querySelector("summary")!).getByText("Forecast")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use for the forecast" })).toBeNull();
  });

  it("isn't offered on a card", () => {
    show(acct({ id: "cc", kind: "credit" }));
    expect(screen.queryByRole("button", { name: "Use for the forecast" })).toBeNull();
  });
});

describe("how a card is paid, from Settings", () => {
  const card = (over: Partial<SettingsAccount> = {}) => acct({ id: "cc", name: "Visa", kind: "credit", pay_from: "chk", ...over });
  const lastBody = () => (vi.mocked(api).mock.calls.at(-1) as [string, { body: Record<string, unknown> }])[1].body;

  it("pays in full by default, with no amount or APR to fill in", () => {
    show(card());
    expect(screen.getByRole("combobox", { name: "Pay" })).toHaveValue("full");
    expect(screen.queryByLabelText("Amount each statement")).toBeNull();
    expect(screen.queryByRole("spinbutton", { name: "APR" })).toBeNull();
    expect(screen.queryByText(/pays/)).toBeNull();
  });

  it("saves the minimum, then asks for the APR and saves it", async () => {
    show(card());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Pay" }), "minimum");
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(lastBody()).toMatchObject({ pay_mode: "minimum", pay_amount: "", apr: "" });
    expect(screen.queryByLabelText("Amount each statement")).toBeNull();
    const apr = screen.getByRole("spinbutton", { name: "APR" });
    await userEvent.type(apr, "24.99");
    await userEvent.tab();
    await waitFor(() => expect(lastBody()).toMatchObject({ pay_mode: "minimum", apr: 24.99 }));
  });

  it("asks for the fixed amount and saves it, and shows it on the account's line", async () => {
    show(card({ pay_mode: "fixed", pay_amount: 300, apr: 19.5 }));
    expect(screen.getByText("pays $300.00 a statement")).toBeInTheDocument();
    const amount = screen.getByLabelText(/Amount each statement/);
    expect(amount).toHaveValue("300");
    expect(amount.parentElement).toHaveTextContent("$");
    expect(screen.getByRole("spinbutton", { name: "APR" })).toHaveValue(19.5);
    await userEvent.clear(amount);
    await userEvent.type(amount, "450");
    await userEvent.tab();
    await waitFor(() => expect(lastBody()).toMatchObject({ pay_mode: "fixed", pay_amount: 450, apr: 19.5 }));
  });

  it("shows a fixed amount with its commas, and saves it without them", async () => {
    show(card({ pay_mode: "fixed", pay_amount: 1850 }));
    const amount = screen.getByLabelText(/Amount each statement/);
    expect(shown(amount)).toBe("1,850");
    await userEvent.clear(amount);
    await userEvent.type(amount, "2100");
    await userEvent.tab();
    await waitFor(() => expect(lastBody()).toMatchObject({ pay_amount: 2100 }));
    expect(shown(amount)).toBe("2,100");
  });

  it("shows the issuer's APR when you haven't entered one, and yours when you have", () => {
    const { unmount } = show(card({ pay_mode: "minimum", issuer_apr: 24.99 }));
    expect(screen.getByRole("spinbutton", { name: "APR" })).toHaveAttribute("placeholder", "24.99");
    expect(screen.getByRole("spinbutton", { name: "APR" })).toHaveValue(null);
    expect(screen.getByText("24.99% from the issuer")).toBeInTheDocument();
    unmount();
    show(card({ pay_mode: "minimum", apr: 18, issuer_apr: 24.99 }));
    expect(screen.getByRole("spinbutton", { name: "APR" })).toHaveValue(18);
    expect(screen.queryByText(/from the issuer/)).toBeNull();
  });

  it("flags a fixed payment with no amount", () => {
    show(card({ pay_mode: "fixed", pay_amount: null }));
    expect(screen.getByText("no fixed amount")).toHaveAttribute("title", "Paid in full until you enter one");
  });

  it("isn't offered on other accounts", () => {
    show(acct());
    expect(screen.queryByRole("combobox", { name: "Pay" })).toBeNull();
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
    // Undo enters it again as it was
    expect(toast).toHaveBeenCalledWith("Statement deleted", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
    await undoOf()();
    expect(api).toHaveBeenLastCalledWith("/api/accounts/cc/statements", { method: "POST",
      body: { statement_date: "2026-08-10", balance: 300, due_date: "2026-09-21", minimum_payment: 25 } });
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("sends no minimum back when undoing the delete of one entered without", async () => {
    show(cc({ statement: { source: "manual", closed: "2026-09-10", due: "2026-10-21", balance: 500, minimum: null, stale: false },
      statements: [{ statement_date: "2026-09-10", balance: 500, due_date: "2026-10-21", minimum_payment: null }] }));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /^Delete the statement that closed Sep.10$/ }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Statement deleted", expect.anything()));
    await undoOf()();
    expect(api).toHaveBeenLastCalledWith("/api/accounts/cc/statements", { method: "POST",
      body: { statement_date: "2026-09-10", balance: 500, due_date: "2026-10-21", minimum_payment: "" } });
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
    expect(dialog).toHaveTextContent("SimpleFIN and Plaid leave it out until you restore it from the bottom of this list, which brings back the account but not what was deleted with it.");
    expect(dialog).not.toHaveTextContent("can’t be undone");
    expect(within(dialog).getByRole("link", { name: "Download a backup first" })).toHaveAttribute("href", "#setup/advanced");
    // more than 50 transactions: the name, typed
    const confirm = within(dialog).getByRole("button", { name: "Delete" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByRole("textbox"), "Savings");
    await userEvent.click(confirm);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/sav/remove", { method: "POST" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(reload).toHaveBeenCalled();
  });
});

describe("deleting an account: what the dialog waits for", () => {
  it("holds Delete back until it knows what goes with the account, and needs no typing for a short history", async () => {
    let answer!: (r: unknown) => void;
    vi.mocked(api).mockImplementation((path: string) => path.endsWith("/removal")
      ? new Promise((r) => { answer = r; }) as never : Promise.resolve({ ok: true }) as never);
    show(acct());
    await userEvent.click(screen.getByRole("button", { name: "Delete account…" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Savings?" });
    expect(dialog).toHaveTextContent("Counting what goes with it…");
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeDisabled();
    answer({ name: "Savings", transactions: 50, recurring: 0, rules: 0, statements: 0, holdings: 0, plaid: false });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Delete" })).toBeEnabled());
    expect(within(dialog).queryByRole("textbox")).toBeNull();
    expect(dialog).toHaveTextContent("Syncs leave it out until you restore it");
  });
});

describe("hiding and renaming an account", () => {
  it("has Rename and Hide in the opened row, not as a checkbox", async () => {
    show(acct({ display_name: "Rainy day" }));
    expect(screen.queryByRole("checkbox", { name: /Hide/ })).toBeNull();
    const actions = within(screen.getByRole("group", { name: "Account actions" }));
    await userEvent.click(actions.getByRole("button", { name: "Rename" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Name"));
  });

  it("hides it, tells the list so the row moves without a reload, and offers Undo", async () => {
    const onhidden = vi.fn();
    render(AccountRow, { a: acct(), cash: [acct()], byName: {}, onhidden });
    await userEvent.click(within(screen.getByRole("group", { name: "Account actions" })).getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(onhidden).toHaveBeenCalledWith("sav", true));
    expect(api).toHaveBeenCalledWith("/api/accounts/sav", { method: "POST", body: { hidden: 1 } });
    expect(reload).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith("Savings hidden", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
    await undoOf()();
    expect(api).toHaveBeenLastCalledWith("/api/accounts/sav", { method: "POST", body: { hidden: 0 } });
    expect(onhidden).toHaveBeenLastCalledWith("sav", false);
  });

  it("keeps it where it is when hiding fails", async () => {
    const onhidden = vi.fn();
    vi.mocked(api).mockRejectedValue(new Error("Nope"));
    render(AccountRow, { a: acct(), cash: [acct()], byName: {}, onhidden });
    await userEvent.click(within(screen.getByRole("group", { name: "Account actions" })).getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Nope"));
    expect(onhidden).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });

  it("shows a hidden one again", async () => {
    const onhidden = vi.fn();
    render(AccountRow, { a: acct({ hidden: true }), cash: [acct()], byName: {}, onhidden });
    await userEvent.click(within(screen.getByRole("group", { name: "Account actions" })).getByRole("button", { name: "Show again" }));
    await waitFor(() => expect(onhidden).toHaveBeenCalledWith("sav", false));
    expect(toast.success).toHaveBeenCalledWith("Savings is shown again");
  });
});

describe("the account's line on a phone", () => {
  it("lets the name take two lines and wraps the line under it between words, keeping the balance on one line", () => {
    show(acct({ id: "cc", name: "Rewards Visa", kind: "credit", pay_from: "chk" }));
    const summary = document.querySelector("summary")!;
    expect(within(summary).getByText("Rewards Visa")).toHaveClass("line-clamp-2");
    expect(summary.innerHTML).not.toContain("overflow-wrap:anywhere");
    expect(within(summary).getByText("$1,000.00")).toHaveClass("whitespace-nowrap");
  });
});

describe("a loan's terms, for the retirement planner", () => {
  const terms = { rate: null, payment: null, source: null, plaid: false, plaid_payment: false, set_rate: null, set_payment: null, inferred_payment: null };
  const loan = (over: Partial<NonNullable<SettingsAccount["loan"]>> = {}) =>
    acct({ id: "mtg", name: "Mortgage", kind: "loan", balance: -250000, loan: { ...terms, ...over } });

  it("lets you set the interest rate and payment, suggesting the payment from recent ones", async () => {
    show(loan({ set_rate: 6.25, inferred_payment: 1840 }));
    const rate = screen.getByRole("spinbutton", { name: /Interest rate/ });
    const payment = screen.getByLabelText(/Monthly payment/);
    expect(rate).toHaveValue(6.25);
    expect(rate.parentElement).toHaveTextContent("%");
    expect(payment).toHaveValue("");
    expect(payment.parentElement).toHaveTextContent("$");
    expect(payment).toHaveAttribute("placeholder", "1,840 from recent payments");
    await userEvent.type(payment, "1900{Enter}");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/mtg",
      { method: "POST", body: { interest_rate: 6.25, monthly_payment: 1900 } }));
    expect(shown(payment)).toBe("1,900");
    expect(screen.queryByText("from Plaid")).toBeNull();
  });

  it("shows the lender's terms from Plaid instead, without fields to change them", () => {
    show(loan({ plaid: true, plaid_payment: true, rate: 6.125, payment: 2140.5, source: "plaid" }));
    expect(screen.queryByLabelText(/Interest rate/)).toBeNull();
    expect(screen.queryByLabelText(/Monthly payment/)).toBeNull();
    expect(screen.getByText("6.125%")).toBeInTheDocument();
    expect(screen.getByText("$2,140.50")).toBeInTheDocument();
    expect(screen.getAllByText("from Plaid")).toHaveLength(2);
  });

  it("lets you set the payment when Plaid gives the rate but no payment, and saves only that", async () => {
    show(loan({ plaid: true, rate: 4, payment: 310, source: "inferred", inferred_payment: 310 }));
    expect(screen.queryByLabelText(/Interest rate/)).toBeNull();
    expect(screen.getByText("4%")).toBeInTheDocument();
    const payment = screen.getByLabelText(/Monthly payment/);
    expect(payment).toHaveAttribute("placeholder", "310 from recent payments");
    await userEvent.type(payment, "325{Enter}");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/mtg", { method: "POST", body: { monthly_payment: 325 } }));
  });

  it("says an empty payment pays the loan off by Plaid's payoff date, when that's what's used", () => {
    show(loan({ plaid: true, rate: 6, payment: 2775.5, source: "plaid", maturity: "2036-09-01", inferred_payment: 1850 }));
    const payment = screen.getByLabelText(/Monthly payment/);
    expect(payment.getAttribute("placeholder")).toMatch(/^2,776 to pay it off by Sep\s1,\s2036$/);
    expect(payment.closest("label")).toHaveAttribute("title", expect.stringContaining("pays the loan off by the date the lender gives"));
  });

  it("isn't asked of other accounts", () => {
    show(acct({ id: "cc", kind: "credit" }));
    expect(screen.queryByText(/Interest rate/)).toBeNull();
  });
});

describe("the Owner choice", () => {
  it("is hidden with one person, and shown with two", () => {
    app.state = { connected: true, owners: ["Alex"], primary_account: null };
    const { unmount } = show(acct());
    expect(screen.queryByLabelText("Owner")).toBeNull();
    unmount();
    app.state = { connected: true, owners: ["Alex", "Sam"], primary_account: null };
    show(acct());
    expect(screen.getByLabelText("Owner")).toBeInTheDocument();
  });

  it("stays when the account's owner isn't the one person", () => {
    app.state = { connected: true, owners: ["Alex"], primary_account: null };
    show(acct({ owner: "Sam" }));
    expect(screen.getByLabelText("Owner")).toBeInTheDocument();
  });
});

describe("the expanded row", () => {
  it("groups its switches in an Options box", () => {
    show(acct());
    const box = screen.getByRole("region", { name: "Options" });
    expect(within(box).getByRole("checkbox", { name: "Count in net worth" })).toBeInTheDocument();
    expect(within(box).queryByRole("checkbox", { name: /Hide/ })).toBeNull();   // Hide is an action at the top
  });
});
