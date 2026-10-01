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
