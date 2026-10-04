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
