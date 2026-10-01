// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { category, tx } from "../../../test/fixtures";
import TxRow from "./TxRow.svelte";
import type { Tx } from "./types";

const props = (t: Tx, extra: Record<string, unknown> = {}) => ({
  t, review: false, selected: false, selecting: false, recurring: [], onselect: vi.fn(), onsave: vi.fn().mockResolvedValue(undefined), onchanged: vi.fn(), ...extra,
});
const row = () => screen.getByRole("listitem");

beforeEach(() => {
  categories.list = [category("Coffee", { icon: "☕" }), category("Groceries", { icon: "🛒" }), category("Salary", { is_income: true })];
  app.state = null;
  vi.mocked(api).mockClear();
});

describe("TxRow", () => {
  it("shows the merchant, the formatted amount and the category", () => {
    render(TxRow, props(tx()));
    expect(within(row()).getByText("Blue Bottle")).toBeInTheDocument();
    expect(within(row()).getByText("-$12.50")).toBeInTheDocument();
    expect(within(row()).getByText("Coffee")).toBeInTheDocument();
  });

  it("gives whatever can truncate a title with its full text", () => {
    render(TxRow, props(tx()));
    expect(within(row()).getByText("Blue Bottle")).toHaveAttribute("title", "Blue Bottle");
    expect(within(row()).getByText("Coffee")).toHaveAttribute("title", "Coffee");
  });

  it("makes money coming in green and bold, and leaves spending plain", () => {
    const { unmount } = render(TxRow, props(tx({ amount: 2500 })));
    expect(screen.getByText("$2,500.00")).toHaveClass("text-emerald-500", "font-semibold");
    unmount();
    render(TxRow, props(tx()));
    expect(screen.getByText("-$12.50")).not.toHaveClass("text-emerald-500");
  });

  it("falls back to the description when there's no merchant name", () => {
    render(TxRow, props(tx({ payee: null, description: "ACH TRANSFER" })));
    expect(within(row()).getByText("ACH TRANSFER")).toBeInTheDocument();
  });

  it("shows the bank's own text only when it says more than the merchant does", () => {
    const { unmount } = render(TxRow, props(tx()));
    expect(screen.getByTitle("BLUE BOTTLE #123")).toBeInTheDocument();
    unmount();
    render(TxRow, props(tx({ payee: "Blue Bottle", description: "BLUE  BOTTLE" })));   // same letters and digits
    expect(screen.queryByTitle("BLUE  BOTTLE")).not.toBeInTheDocument();
  });

  describe("logo", () => {
    it("shows the merchant's logo when it has one", () => {
      const { container } = render(TxRow, props(tx({ logo: "/logos/bb.png" })));
      expect(container.querySelector("img")).toHaveAttribute("src", "/logos/bb.png");
    });

    it("shows the merchant's first letter when it doesn't", () => {
      const { container } = render(TxRow, props(tx({ logo: null })));
      expect(container.querySelector("img")).toBeNull();
      expect(screen.getByText("B", { selector: "span[aria-hidden=true]" })).toBeInTheDocument();
    });

    it("skips leading punctuation when picking the letter, and uses ? if there's nothing", () => {
      const { unmount } = render(TxRow, props(tx({ payee: "*sq Joe's" })));
      expect(screen.getByText("S", { selector: "span[aria-hidden=true]" })).toBeInTheDocument();
      unmount();
      render(TxRow, props(tx({ payee: null, description: null })));
      expect(screen.getByText("?", { selector: "span[aria-hidden=true]" })).toBeInTheDocument();
    });
  });

  describe("badges", () => {
    it("flags a pending charge", () => {
      render(TxRow, props(tx({ pending: 1 })));
      expect(screen.getByText("pending")).toBeInTheDocument();
    });

    it("flags what needs review, except on the Review tab where everything does", () => {
      const { unmount } = render(TxRow, props(tx({ needs_review: 1 })));
      expect(screen.getByText("review")).toBeInTheDocument();
      unmount();
      render(TxRow, props(tx({ needs_review: 1 }), { review: true }));
      expect(screen.queryByText("review")).not.toBeInTheDocument();
    });
  });

  describe("category", () => {
    it("asks for one when there is none", () => {
      render(TxRow, props(tx({ category: null })));
      expect(within(row()).getByText("Choose category")).toBeInTheDocument();
    });

    it("saves as soon as you pick another", async () => {
      const p = props(tx());
      render(TxRow, p);
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Blue Bottle" }), "Groceries");
      expect(p.onsave).toHaveBeenCalledWith("Groceries");
    });

    it("doesn't save when the blank option is picked", async () => {
      const p = props(tx());
      render(TxRow, p);
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for Blue Bottle" }), "");
      expect(p.onsave).not.toHaveBeenCalled();
    });

    it("shows the AI's confidence and lets you keep its suggestion", async () => {
      const p = props(tx({ needs_review: 1, category_source: "ai", confidence: 0.87 }));
      render(TxRow, p);
      expect(screen.getByText("87%")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /Keep/ }));
      expect(p.onsave).toHaveBeenCalledWith("Coffee");
    });

    it("doesn't offer to keep a category you chose yourself", () => {
      render(TxRow, props(tx({ needs_review: 1, category_source: "manual" })));
      expect(screen.queryByRole("button", { name: /Keep/ })).not.toBeInTheDocument();
    });
  });

  describe("split", () => {
    const split = tx({ is_split: 1, category: null, amount: -30, splits: [{ category: "Groceries", amount: -20 }, { category: "Coffee", amount: -10, note: "beans" }] });

    it("lists each part's category and amount instead of a single category, without a split badge", () => {
      render(TxRow, props(split));
      expect(screen.queryByText("split")).not.toBeInTheDocument();
      expect(screen.getByText("Groceries $20.00")).toBeInTheDocument();
      expect(screen.getByText("Coffee $10.00")).toHaveAttribute("title", "beans");
      expect(screen.queryByRole("combobox", { name: /Category for/ })).not.toBeInTheDocument();
    });

    it("opens the split editor when clicked", async () => {
      render(TxRow, props(split));
      await userEvent.click(screen.getByTitle("Edit the split"));
      expect(screen.getByText("Split $30.00")).toBeInTheDocument();
    });

    it("offers to split an ordinary transaction, and closes the editor on Cancel", async () => {
      render(TxRow, props(tx()));
      await userEvent.click(screen.getByRole("button", { name: "Split" }));
      expect(screen.getByText("Split $12.50")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByText("Split $12.50")).not.toBeInTheDocument();
    });
  });

  describe("selecting", () => {
    it("reports a checked box along with the click, so shift-click ranges can work", async () => {
      const p = props(tx());
      render(TxRow, p);
      await userEvent.click(screen.getByRole("checkbox", { name: "Select Blue Bottle" }));
      expect(p.onselect).toHaveBeenCalledWith(expect.any(MouseEvent), true);
    });

    it("highlights a selected row", () => {
      render(TxRow, props(tx(), { selected: true }));
      expect(row()).toHaveClass("bg-primary/15");
      expect(screen.getByRole("checkbox")).toBeChecked();
    });
  });

  describe("recurring link", () => {
    it("names the recurring item a transaction is linked to", () => {
      render(TxRow, props(tx({ recurring_id: 5, recurring_name: "Coffee club" })));
      expect(screen.getByRole("button", { name: "Recurring: Coffee club (click to change)" })).toHaveTextContent("Coffee club");
    });

    it("opens the picker to link one, and saves the choice", async () => {
      const p = props(tx(), { recurring: [{ id: 5, name: "Coffee club", frequency: "monthly", account_id: "a1" }] });
      render(TxRow, p);
      await userEvent.click(screen.getByRole("button", { name: "Link to a recurring item" }));
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Recurring item for this transaction" }), "5");
      expect(api).toHaveBeenCalledWith("/api/transactions/t1/recurring", { method: "POST", body: { recurring_id: 5 } });
      expect(p.onchanged).toHaveBeenCalled();
    });
  });

  describe("retail order", () => {
    const withOrder = tx({ retail: { order_id: "o1", retailer: "amazon", items: 3 } });

    it("shows a receipt badge, labelled with the order, and only loads its items when you open it", async () => {
      vi.mocked(api).mockResolvedValue({ id: "o1", retailer: "amazon", order_number: "111", items: [], charges: [], url: "" });
      render(TxRow, props(withOrder));
      const toggle = screen.getByRole("button", { name: "Receipt: Amazon · 3 items" });
      expect(within(toggle).getByText("receipt")).toBeInTheDocument();
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(api).not.toHaveBeenCalled();
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(api).toHaveBeenCalledWith("/api/retail/orders/o1", { keep: true });
      await userEvent.click(toggle);   // leave it closed for other tests: open orders are remembered across rows
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    });
  });

  // jsdom has no layout, so these check the classes that keep a row's text from landing on its neighbours.
  describe("narrow rows", () => {
    it("hides the account name and recurring name in phone landscape only below lg, not on short desktop windows", () => {
      const landscape = "max-lg:[@media(max-height:500px)]";
      render(TxRow, props(tx({ account_name: "Shared Checking", recurring_id: 2, recurring_name: "Rent" })));
      expect(within(row()).getByText("Rent")).toHaveClass(`${landscape}:hidden!`);
      for (const e of within(row()).getAllByText("Shared Checking")) expect(e.className).not.toMatch(/(^|\s)\[@media\(max-height:500px\)\]/);
    });

    it("shows the receipt badge as just its icon unless the cell has room for the word", () => {
      render(TxRow, props(tx({ retail: { order_id: "o1", retailer: "target", channel: "store", items: 3 } })));
      const chip = screen.getByRole("button", { name: "Receipt: Target in store · 3 items" });
      expect(chip).toHaveClass("shrink-0");
      expect(within(chip).getByText("receipt")).toHaveClass("hidden", "@sm/title:inline", "max-sm:inline");
      expect(within(row()).getByText("Blue Bottle")).toHaveClass("min-w-[6ch]", "truncate");
    });

    it("shows the pending and review badges as an icon (labelled) unless the cell has room for the word", () => {
      render(TxRow, props(tx({ pending: 1, needs_review: 1 })));
      for (const word of ["pending", "review"]) {
        const badge = within(row()).getByTitle(word);
        expect(within(badge).getByLabelText(word)).toHaveClass("@sm/title:hidden");
        expect(within(badge).getByText(word)).toHaveClass("hidden", "@sm/title:inline");
        expect(badge).toHaveClass("shrink-0");
      }
    });

    it("lets the account and the bank's text share a line, hiding the text until its cell has room", () => {
      render(TxRow, props(tx({ account_name: "Shared Checking" })));
      const account = within(row()).getAllByText("Shared Checking")[0].parentElement!.parentElement!;
      expect(account).toHaveClass("min-w-0", "shrink-[4]");
      const bankText = within(row()).getByTitle("BLUE BOTTLE #123");
      expect(bankText).toHaveClass("hidden", "min-w-0", "truncate", "@sm/acct:block");
      expect(bankText.parentElement).toHaveClass("@container/acct", "min-w-0");
    });

    it("shows only the account's logo on a phone", () => {
      app.state = { connected: true, brands: { a1: { institution: "SimpleFIN Bridge", initial: "S" } } };
      render(TxRow, props(tx({ account_name: "Shared Checking" })));
      const logo = within(row()).getAllByTitle("Shared Checking").find((e) => e.classList.contains("md:hidden"))!;
      expect(within(logo).getByText("Shared Checking")).toHaveClass("hidden");
    });
  });

  describe("details (from lg up)", () => {
    it("opens the details, with the account, its institution and the bank's text, and closes them again", async () => {
      app.state = { connected: true, brands: { a1: { institution: "SimpleFIN Bridge", initial: "S" } } };
      render(TxRow, props(tx()));
      const toggle = screen.getByRole("button", { name: "Details for Blue Bottle" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByText("SimpleFIN Bridge")).not.toBeInTheDocument();
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByText("SimpleFIN Bridge")).toBeInTheDocument();
      expect(screen.getByText("BLUE BOTTLE #123", { selector: "dd" })).toBeInTheDocument();
      await userEvent.click(toggle);
      expect(screen.queryByText("SimpleFIN Bridge")).not.toBeInTheDocument();
    });

    const amazon = (using: "brand" | "bank" = "brand") => tx({ payee: using === "brand" ? "Amazon" : "Amzn Mktp Us", description: "AMZN Mktp US*2K3AB1",
      brand: { brand: "Amazon", bank_name: "Amzn Mktp Us", using } });

    it("offers the bank's name only for a brand's name a sync gave it", async () => {
      render(TxRow, props(tx()));
      await userEvent.click(screen.getByRole("button", { name: "Details for Blue Bottle" }));
      expect(screen.queryByRole("button", { name: "Use the bank’s name" })).not.toBeInTheDocument();
    });

    it("uses the bank's name for one transaction, and undoes it", async () => {
      const p = props(amazon());
      render(TxRow, p);
      await userEvent.click(screen.getByRole("button", { name: "Details for Amazon" }));
      expect(screen.getByRole("button", { name: "Use the bank’s name" })).toHaveAttribute("title", "Rename it “Amzn Mktp Us”");
      const was = [{ id: "t1", payee: "Amazon" }];
      vi.mocked(api).mockResolvedValueOnce({ updated: 1, payee: "Amzn Mktp Us", was, keep_bank: { brand: "Amazon", keep: false } });
      await userEvent.click(screen.getByRole("button", { name: "Use the bank’s name" }));
      await userEvent.click(screen.getByRole("button", { name: "Just this one" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/t1/name", { method: "POST", body: { use: "bank", all: false } });
      expect(p.onchanged).toHaveBeenCalledTimes(1);
      const [message, opts] = vi.mocked(toast).mock.calls.at(-1)!;
      expect(message).toBe("Amazon → Amzn Mktp Us");
      await (opts as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
      // Just the names: the brand's setting didn't change
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: was } });
      expect(p.onchanged).toHaveBeenCalledTimes(2);
    });

    it("keeps the bank's names for all of the brand's from now on, and undo puts the setting back too", async () => {
      render(TxRow, props(amazon()));
      await userEvent.click(screen.getByRole("button", { name: "Details for Amazon" }));
      const was = [{ id: "t1", payee: "Amazon" }, { id: "t2", payee: "Amazon" }];
      vi.mocked(api).mockResolvedValueOnce({ updated: 2, payee: "Amzn Mktp Us", was, keep_bank: { brand: "Amazon", keep: false } });
      await userEvent.click(screen.getByRole("button", { name: "Use the bank’s name" }));
      await userEvent.click(screen.getByRole("button", { name: "All Amazon, from now on" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/t1/name", { method: "POST", body: { use: "bank", all: true } });
      const [message, opts] = vi.mocked(toast).mock.calls.at(-1)!;
      expect(message).toBe("Amazon: the bank’s names from now on");
      expect(opts).toMatchObject({ description: "2 renamed" });
      await (opts as unknown as { action: { onClick: () => Promise<void> } }).action.onClick();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk",
        { method: "POST", body: { restore: was, keep_bank: { brand: "Amazon", keep: false } } });
    });

    it("goes back to the brand's name, and says so when that fails", async () => {
      const p = props(amazon("bank"));
      render(TxRow, p);
      await userEvent.click(screen.getByRole("button", { name: "Details for Amzn Mktp Us" }));
      vi.mocked(api).mockRejectedValueOnce(new Error("This transaction’s name isn’t a brand’s"));
      await userEvent.click(screen.getByRole("button", { name: "Use “Amazon”" }));
      await userEvent.click(screen.getByRole("button", { name: "Just this one" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/t1/name", { method: "POST", body: { use: "brand", all: false } });
      expect(toast.error).toHaveBeenCalledWith("This transaction’s name isn’t a brand’s");
      expect(p.onchanged).not.toHaveBeenCalled();
    });
  });
});
