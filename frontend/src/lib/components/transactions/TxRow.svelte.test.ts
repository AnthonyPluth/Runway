// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
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

    it("lists each part's category and amount instead of a single category", () => {
      render(TxRow, props(split));
      expect(screen.getByText("split")).toBeInTheDocument();
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

    it("labels the order and only loads its items when you open it", async () => {
      vi.mocked(api).mockResolvedValue({ id: "o1", retailer: "amazon", order_number: "111", items: [], charges: [], url: "" });
      render(TxRow, props(withOrder));
      const toggle = screen.getByRole("button", { name: /Amazon · 3 items/ });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(api).not.toHaveBeenCalled();
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(api).toHaveBeenCalledWith("/api/retail/orders/o1", { keep: true });
      await userEvent.click(toggle);   // leave it closed for other tests: open orders are remembered across rows
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    });
  });
});
