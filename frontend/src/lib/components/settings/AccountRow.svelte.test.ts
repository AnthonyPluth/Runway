// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { owners: [], primary_account: null }, version: 0 }, reload: vi.fn(), refreshState: vi.fn() }));

import { api } from "$lib/api";
import { app, refreshState } from "$lib/app.svelte";
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

describe("a loan's terms, for the retirement planner", () => {
  const terms = { rate: null, payment: null, source: null, plaid: false, set_rate: null, set_payment: null, inferred_payment: null };
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
    show(loan({ plaid: true, rate: 6.125, payment: 2140.5, source: "plaid" }));
    expect(screen.queryByRole("textbox", { name: /Interest rate/ })).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Monthly payment/ })).toBeNull();
    expect(screen.getByText("6.125%")).toBeInTheDocument();
    expect(screen.getByText("$2,140.50")).toBeInTheDocument();
    expect(screen.getAllByText("from Plaid")).toHaveLength(2);
  });

  it("says when Plaid's rate comes with a payment worked out from recent ones", () => {
    show(loan({ plaid: true, rate: 4, payment: 310, source: "inferred" }));
    expect(screen.getByText("from recent payments")).toBeInTheDocument();
  });

  it("isn't asked of other accounts", () => {
    show(acct({ id: "cc", kind: "credit" }));
    expect(screen.queryByText(/Interest rate/)).toBeNull();
  });
});
