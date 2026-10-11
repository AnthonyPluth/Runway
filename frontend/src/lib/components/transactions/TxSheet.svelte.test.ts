// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { category, tx } from "../../../test/fixtures";
import TxSheet from "./TxSheet.svelte";
import type { Account } from "$lib/types";
import type { Tx } from "./types";
import { pickCategory, pickedValue } from "../../../test/pick";

const accounts: Account[] = [{ id: "a1", name: "Checking", kind: "checking" }, { id: "c1", name: "Card", kind: "credit" }, { id: "inv", name: "Brokerage", kind: "investment" }];
const setup = (t: Tx | null, extra: Record<string, unknown> = {}) => {
  const p = { open: true, t, accounts, recurring: [], onsave: vi.fn().mockResolvedValue(true), onchanged: vi.fn(), ...extra };
  render(TxSheet, p);
  return p;
};
const undo = () => (vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
const field = (name: string) => screen.getByLabelText(name) as HTMLInputElement;
async function change(name: string, value: string) {
  const el = field(name);
  await userEvent.clear(el);
  if (value) await userEvent.type(el, value);
  el.blur();
}

beforeEach(() => {
  categories.list = [category("Coffee"), category("Groceries"), category("Transfer", { is_transfer: 1 }), category("Ignore", { is_transfer: 1 })];
  app.state = { connected: true, brands: { a1: { institution: "First Bank", initial: "F" } } };
  vi.mocked(api).mockReset().mockResolvedValue({});
  vi.mocked(toast).mockClear(); vi.mocked(toast.error).mockClear(); vi.mocked(toast.success).mockClear();
});

describe("transaction sheet", () => {
  it("shows the transaction, where it came from and the bank's text", () => {
    setup(tx({ source: "simplefin" }));
    expect(screen.getByRole("dialog", { name: "Blue Bottle" })).toBeInTheDocument();
    expect(screen.getByText("−$12.50")).toBeInTheDocument();
    expect(screen.getByText("First Bank via SimpleFIN")).toBeInTheDocument();
    expect(screen.getByText("BLUE BOTTLE #123")).toBeInTheDocument();
    expect(field("Name")).toHaveValue("Blue Bottle");
    expect(field("Date")).toHaveValue("2026-03-10");
    expect(field("Amount")).toHaveValue(12.5);
  });

  it("says who set the category in words", () => {
    setup(tx({ category_source: "rule" }));
    expect(screen.getByText("Set by a rule")).toBeInTheDocument();
  });

  it("renames it when you leave the field, and Undo puts back exactly what was there", async () => {
    const was = { id: "t1", payee: "Blue Bottle", posted: "2026-03-10", amount: -12.5, notes: null };
    vi.mocked(api).mockResolvedValueOnce({ was });
    const p = setup(tx());
    await change("Name", "Blue Bottle Coffee");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/t1", { method: "POST", body: { payee: "Blue Bottle Coffee" } }));
    expect(toast).toHaveBeenLastCalledWith("Blue Bottle → Blue Bottle Coffee", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
    expect(p.onchanged).toHaveBeenCalled();
    await undo();
    expect(api).toHaveBeenCalledWith("/api/transactions/t1", { method: "POST", body: { restore: was } });
  });

  it("changes the amount and its direction, keeping the sign", async () => {
    vi.mocked(api).mockResolvedValue({ was: {} });
    setup(tx());
    await change("Amount", "14");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/t1", { method: "POST", body: { amount: -14 } }));
    await userEvent.click(screen.getByRole("radio", { name: "In" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/t1", { method: "POST", body: { amount: 14 } }));
  });

  it("says what's wrong under the field instead of saving", async () => {
    setup(tx());
    await change("Date", "");
    expect(await screen.findByText("Enter a date")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("shows the server's refusal under the field", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Enter a date like 2026-09-30"));
    setup(tx());
    await change("Date", "2026-03-11");
    expect(await screen.findByText("Enter a date like 2026-09-30")).toBeInTheDocument();
    expect(toast).not.toHaveBeenCalled();
  });

  it("keeps a pending one's date and amount the bank's, but its note yours", () => {
    setup(tx({ pending: 1, source: "plaid" }));
    expect(field("Date")).toBeDisabled();
    expect(field("Amount")).toBeDisabled();
    expect(field("Note")).not.toBeDisabled();
  });

  it("marks a date or amount you changed, with the bank's on hover", () => {
    setup(tx({ amount: -14, bank_amount: -12.5 }));
    expect(screen.getByText("edited")).toHaveAttribute("title", "Edited · the bank’s: −$12.50");
  });

  it("saves a note", async () => {
    vi.mocked(api).mockResolvedValue({ was: {} });
    setup(tx());
    await change("Note", "for the office");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/t1", { method: "POST", body: { notes: "for the office" } }));
    expect(toast).toHaveBeenLastCalledWith("Note added", expect.anything());
  });

  it("changes the category as the row does, and shows the saved one again when that fails", async () => {
    const p = setup(tx(), { onsave: vi.fn().mockResolvedValue(false) });
    await pickCategory(screen.getByRole("combobox", { name: /^Category(:|$)/ }), "Groceries");
    expect(p.onsave).toHaveBeenCalledWith(expect.objectContaining({ id: "t1" }), "Groceries");
    await waitFor(() => expect(pickedValue(screen.getByRole("combobox", { name: /^Category(:|$)/ }))).toBe("Coffee"));
  });

  it("excludes it with a switch (Ignore), and turning it off brings back the category it had", async () => {
    const p = setup(tx());
    const exclude = screen.getByRole("switch", { name: "Exclude from reports and budgets" });
    expect(exclude).toHaveAttribute("aria-checked", "false");
    await userEvent.click(exclude);
    expect(p.onsave).toHaveBeenLastCalledWith(expect.anything(), "Ignore");
  });

  it("marks a transfer, and turning that off with nothing to go back to leaves it uncategorized", async () => {
    vi.mocked(api).mockResolvedValue({ was: {} });
    const p = setup(tx({ category: "Transfer" }));
    const transfer = screen.getByRole("switch", { name: "This is a transfer" });
    expect(transfer).toHaveAttribute("aria-checked", "true");
    await userEvent.click(transfer);
    expect(p.onsave).not.toHaveBeenCalled();
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/t1", { method: "POST", body: { category: null } }));
  });

  describe("a brand's name", () => {
    const amazon = (using: "brand" | "bank" = "brand") => tx({ payee: using === "brand" ? "Amazon" : "Amzn Mktp Us", description: "AMZN Mktp US*2K3AB1",
      brand: { brand: "Amazon", bank_name: "Amzn Mktp Us", using } });

    it("isn't offered for an ordinary name", () => {
      setup(tx());
      expect(screen.queryByRole("button", { name: "Use the bank’s name" })).not.toBeInTheDocument();
    });

    it("uses the bank's name for one transaction, and undoes it", async () => {
      const p = setup(amazon());
      expect(screen.getByRole("button", { name: "Use the bank’s name" })).toHaveAttribute("title", "Rename it “Amzn Mktp Us”");
      const was = [{ id: "t1", payee: "Amazon" }];
      vi.mocked(api).mockResolvedValueOnce({ updated: 1, payee: "Amzn Mktp Us", was, keep_bank: { brand: "Amazon", keep: false } });
      await userEvent.click(screen.getByRole("button", { name: "Use the bank’s name" }));
      await userEvent.click(screen.getByRole("button", { name: "Just this one" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/t1/name", { method: "POST", body: { use: "bank", all: false } });
      expect(p.onchanged).toHaveBeenCalledTimes(1);
      expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("Amazon → Amzn Mktp Us");
      await undo();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: was } });
    });

    it("keeps the bank's names for all of the brand's from now on, and undo puts the setting back too", async () => {
      setup(amazon());
      const was = [{ id: "t1", payee: "Amazon" }, { id: "t2", payee: "Amazon" }];
      vi.mocked(api).mockResolvedValueOnce({ updated: 2, payee: "Amzn Mktp Us", was, keep_bank: { brand: "Amazon", keep: false } });
      await userEvent.click(screen.getByRole("button", { name: "Use the bank’s name" }));
      await userEvent.click(screen.getByRole("button", { name: "All Amazon, from now on" }));
      expect(vi.mocked(toast).mock.calls.at(-1)).toEqual(["Amazon: the bank’s names from now on", expect.objectContaining({ description: "2 renamed" })]);
      await undo();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: was, keep_bank: { brand: "Amazon", keep: false } } });
    });

    it("says so when going back to the brand's name fails", async () => {
      const p = setup(amazon("bank"));
      vi.mocked(api).mockRejectedValueOnce(new Error("This transaction’s name isn’t a brand’s"));
      await userEvent.click(screen.getByRole("button", { name: "Use “Amazon”" }));
      await userEvent.click(screen.getByRole("button", { name: "Just this one" }));
      expect(toast.error).toHaveBeenCalledWith("This transaction’s name isn’t a brand’s");
      expect(p.onchanged).not.toHaveBeenCalled();
    });
  });

  it("opens the split editor in the sheet", async () => {
    setup(tx());
    await userEvent.click(screen.getByRole("button", { name: "Split" }));
    expect(screen.getByText("Split $12.50")).toBeInTheDocument();
  });

  it("deletes a transaction you added, after asking", async () => {
    const p = setup(tx({ id: "a1|manual:x", source: "manual" }));
    expect(screen.getByText("Added by you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete transaction" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/a1%7Cmanual%3Ax", { method: "DELETE" }));
    expect(p.onchanged).toHaveBeenCalled();
  });

  it("doesn't offer to delete a bank's transaction", () => {
    setup(tx({ source: "plaid" }));
    expect(screen.queryByRole("button", { name: "Delete transaction" })).not.toBeInTheDocument();
  });
});

describe("adding a transaction", () => {
  it("starts on today, money out, in the account the list is filtered to", () => {
    setup(null, { account: "c1" });
    expect(screen.getByRole("dialog", { name: "Add a transaction" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Account" })).toHaveValue("c1");
    expect(screen.queryByRole("option", { name: "Brokerage" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Money out" })).toHaveAttribute("aria-checked", "true");
  });

  it("says what's missing instead of sending it", async () => {
    setup(null);
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText("Give it a name")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "Market");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText("Enter the amount")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("adds it, and Undo deletes it again", async () => {
    vi.mocked(api).mockResolvedValueOnce({ id: "a1|manual:9" });
    const p = setup(null);
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "Farmers market");
    await userEvent.type(screen.getByLabelText("Amount"), "23.40");
    await pickCategory(screen.getByRole("combobox", { name: /^Category(:|$)/ }), "Groceries");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions", { method: "POST", body: expect.objectContaining({
      account: "a1", payee: "Farmers market", amount: -23.4, category: "Groceries", notes: "" }) }));
    expect(p.onchanged).toHaveBeenCalled();
    expect(vi.mocked(toast).mock.calls.at(-1)![0]).toBe("Added Farmers market");
    await undo();
    expect(api).toHaveBeenCalledWith("/api/transactions/a1%7Cmanual%3A9", { method: "DELETE" });
  });

  it("keeps the form and says why when it can't be added", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Choose an account"));
    setup(null);
    await userEvent.type(screen.getByRole("textbox", { name: "Name" }), "Cash");
    await userEvent.type(screen.getByLabelText("Amount"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Choose an account")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Cash");
  });
});
