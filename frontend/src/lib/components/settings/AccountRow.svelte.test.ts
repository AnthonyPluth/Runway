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

describe("how a card is paid, from Settings", () => {
  const card = (over: Partial<SettingsAccount> = {}) => acct({ id: "cc", name: "Visa", kind: "credit", pay_from: "chk", ...over });
  const lastBody = () => (vi.mocked(api).mock.calls.at(-1) as [string, { body: Record<string, unknown> }])[1].body;

  it("pays in full by default, with no amount or APR to fill in", () => {
    show(card());
    expect(screen.getByRole("combobox", { name: "Pay" })).toHaveValue("full");
    expect(screen.queryByRole("spinbutton", { name: "Amount each statement" })).toBeNull();
    expect(screen.queryByRole("spinbutton", { name: "APR (%)" })).toBeNull();
    expect(screen.queryByText(/pays/)).toBeNull();
  });

  it("saves the minimum, then asks for the APR and saves it", async () => {
    show(card());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Pay" }), "minimum");
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(lastBody()).toMatchObject({ pay_mode: "minimum", pay_amount: "", apr: "" });
    expect(screen.queryByRole("spinbutton", { name: "Amount each statement" })).toBeNull();
    const apr = screen.getByRole("spinbutton", { name: "APR (%)" });
    await userEvent.type(apr, "24.99");
    await userEvent.tab();
    await waitFor(() => expect(lastBody()).toMatchObject({ pay_mode: "minimum", apr: 24.99 }));
  });

  it("asks for the fixed amount and saves it, and shows it on the account's line", async () => {
    show(card({ pay_mode: "fixed", pay_amount: 300, apr: 19.5 }));
    expect(screen.getByText("pays $300.00 a statement")).toBeInTheDocument();
    const amount = screen.getByRole("spinbutton", { name: "Amount each statement" });
    expect(amount).toHaveValue(300);
    expect(screen.getByRole("spinbutton", { name: "APR (%)" })).toHaveValue(19.5);
    await userEvent.clear(amount);
    await userEvent.type(amount, "450");
    await userEvent.tab();
    await waitFor(() => expect(lastBody()).toMatchObject({ pay_mode: "fixed", pay_amount: 450, apr: 19.5 }));
  });

  it("shows the issuer's APR when you haven't entered one, and yours when you have", () => {
    const { unmount } = show(card({ pay_mode: "minimum", issuer_apr: 24.99 }));
    expect(screen.getByRole("spinbutton", { name: "APR (%)" })).toHaveAttribute("placeholder", "24.99");
    expect(screen.getByRole("spinbutton", { name: "APR (%)" })).toHaveValue(null);
    expect(screen.getByText("24.99% from the issuer")).toBeInTheDocument();
    unmount();
    show(card({ pay_mode: "minimum", apr: 18, issuer_apr: 24.99 }));
    expect(screen.getByRole("spinbutton", { name: "APR (%)" })).toHaveValue(18);
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

describe("a loan's terms, for the retirement planner", () => {
  const terms = { rate: null, payment: null, source: null, plaid: false, plaid_payment: false, set_rate: null, set_payment: null, inferred_payment: null };
  const loan = (over: Partial<NonNullable<SettingsAccount["loan"]>> = {}) =>
    acct({ id: "mtg", name: "Mortgage", kind: "loan", balance: -250000, loan: { ...terms, ...over } });

  it("lets you set the interest rate and payment, suggesting the payment from recent ones", async () => {
    show(loan({ set_rate: 6.25, inferred_payment: 1840 }));
    const rate = screen.getByRole("textbox", { name: /Interest rate/ });
    const payment = screen.getByRole("textbox", { name: /Monthly payment/ });
    expect(rate).toHaveValue("6.25");
    expect(payment).toHaveValue("");
    expect(payment).toHaveAttribute("placeholder", "1,840 from recent payments");
    await userEvent.type(payment, "1,900{Enter}");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/mtg",
      { method: "POST", body: { interest_rate: "6.25", monthly_payment: "1,900" } }));
    expect(screen.queryByText("from Plaid")).toBeNull();
  });

  it("shows the lender's terms from Plaid instead, without fields to change them", () => {
    show(loan({ plaid: true, plaid_payment: true, rate: 6.125, payment: 2140.5, source: "plaid" }));
    expect(screen.queryByRole("textbox", { name: /Interest rate/ })).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Monthly payment/ })).toBeNull();
    expect(screen.getByText("6.125%")).toBeInTheDocument();
    expect(screen.getByText("$2,140.50")).toBeInTheDocument();
    expect(screen.getAllByText("from Plaid")).toHaveLength(2);
  });

  it("lets you set the payment when Plaid gives the rate but no payment, and saves only that", async () => {
    show(loan({ plaid: true, rate: 4, payment: 310, source: "inferred", inferred_payment: 310 }));
    expect(screen.queryByRole("textbox", { name: /Interest rate/ })).toBeNull();
    expect(screen.getByText("4%")).toBeInTheDocument();
    const payment = screen.getByRole("textbox", { name: /Monthly payment/ });
    expect(payment).toHaveAttribute("placeholder", "310 from recent payments");
    await userEvent.type(payment, "325{Enter}");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/accounts/mtg", { method: "POST", body: { monthly_payment: "325" } }));
  });

  it("says an empty payment pays the loan off by Plaid's payoff date, when that's what's used", () => {
    show(loan({ plaid: true, rate: 6, payment: 2775.5, source: "plaid", maturity: "2036-09-01", inferred_payment: 1850 }));
    const payment = screen.getByRole("textbox", { name: /Monthly payment/ });
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
    expect(within(box).getByRole("checkbox", { name: "Hide this account" })).toBeInTheDocument();
  });
});
