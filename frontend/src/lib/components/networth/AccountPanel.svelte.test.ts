// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import AccountPanel from "./AccountPanel.svelte";

const loan = { id: "mtg", name: "Mortgage", org: "Bank", balance: 241234.5, as_of: "2026-03-23", counted: true };

describe("an account's panel on Net worth", () => {
  it("shows a loan paid down since its last balance, with what that balance was", async () => {
    render(AccountPanel, { open: true, acct: { ...loan, synced: 250000 }, onchange: () => {} });
    expect(await screen.findByText("$241,234.50")).toBeInTheDocument();
    expect(screen.getByText("Balance then").nextElementSibling).toHaveTextContent("$250,000.00");
    expect(screen.getByText(/Paid down since on its interest rate and monthly payment/)).toBeInTheDocument();
  });

  it("says nothing about paying down when the balance is as synced", async () => {
    render(AccountPanel, { open: true, acct: loan, onchange: () => {} });
    expect(await screen.findByText("$241,234.50")).toBeInTheDocument();
    expect(screen.queryByText("Balance then")).toBeNull();
    expect(screen.queryByText(/Paid down since/)).toBeNull();
  });
});
