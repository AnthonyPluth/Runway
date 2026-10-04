// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: { owners: [], primary_account: null }, version: 0 }, reload: vi.fn(), refreshState: vi.fn() }));

import { api } from "$lib/api";
import type { SettingsAccount } from "./types";
import { acct, resetRow, shown, show } from "../../../test/accountRow";

beforeEach(resetRow);

describe("a loan's terms, for the retirement planner", () => {
  const terms = { rate: null, payment: null, maturity: null, source: null, plaid: false, plaid_payment: false, set_rate: null, set_payment: null, inferred_payment: null };
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
