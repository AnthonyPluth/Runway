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
import { pickCategory, pickedValue } from "../../../test/pick";
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
    expect(within(row()).getByText("−$12.50")).toBeInTheDocument();
    expect(within(row()).getByText("Coffee")).toBeInTheDocument();
  });

  it("under a category filter, shows a split one's part: its amount of the whole, and a picker for just that part", async () => {
    const p = props(tx({ amount: -100, is_split: 1, splits: [{ category: "Groceries", amount: -60 }, { category: "Coffee", amount: -40 }],
      match: { amount: -60, categories: ["Groceries"] } }), { family: ["Groceries"] });
    render(TxRow, p);
    expect(within(row()).getByText("−$60.00")).toBeInTheDocument();
    expect(within(row()).getByText("of $100.00")).toBeInTheDocument();
    expect(within(row()).queryByText(/\$40\.00/)).not.toBeInTheDocument();
    await pickCategory(screen.getByRole("combobox", { name: "Category for the Groceries part of Blue Bottle" }), "Coffee");
    expect(p.onsave).toHaveBeenCalledWith("Coffee");
  });

  it("leaves the account out of the row when the list is filtered to it", () => {
    render(TxRow, props(tx(), { oneAccount: true }));
    expect(within(row()).queryByTitle("Checking")).not.toBeInTheDocument();
  });

  it("gives whatever can truncate a title with its full text", () => {
    render(TxRow, props(tx()));
    expect(within(row()).getByText("Blue Bottle")).toHaveAttribute("title", "Blue Bottle");
    expect(within(row()).getByText("Coffee")).toHaveAttribute("title", "Coffee");
  });

  it("makes money coming in green and bold, and leaves spending plain", () => {
    const { unmount } = render(TxRow, props(tx({ amount: 2500 })));
    expect(screen.getByText("+$2,500.00")).toHaveClass("text-good", "font-semibold");
    unmount();
    render(TxRow, props(tx()));
    expect(screen.getByText("−$12.50")).not.toHaveClass("text-good");
  });

  it("falls back to the description when there's no merchant name", () => {
    render(TxRow, props(tx({ payee: null, description: "ACH TRANSFER" })));
    expect(within(row()).getByText("ACH TRANSFER")).toBeInTheDocument();
  });

  it("shows the bank's own text only when it says more than the merchant does", () => {
    const { unmount } = render(TxRow, props(tx()));
    expect(screen.getByTitle("BLUE BOTTLE #123")).toBeInTheDocument();
    unmount();
    render(TxRow, props(tx({ payee: "Blue Bottle", description: "BLUE  BOTTLE" })));
    expect(screen.queryByTitle("BLUE  BOTTLE")).not.toBeInTheDocument();
  });

  it("opens the details (the sheet) from the name and from the chevron, at every width", async () => {
    const onopen = vi.fn();
    render(TxRow, props(tx(), { onopen }));
    await userEvent.click(within(row()).getByRole("button", { name: "Blue Bottle" }));
    await userEvent.click(within(row()).getByRole("button", { name: "Details for Blue Bottle" }));
    expect(onopen).toHaveBeenCalledTimes(2);
    expect(within(row()).getByRole("button", { name: "Details for Blue Bottle" })).not.toHaveClass("hidden");
  });

  it("dims a pending amount as well as badging it", () => {
    render(TxRow, props(tx({ pending: 1 })));
    expect(within(row()).getByText("−$12.50")).toHaveClass("opacity-70");
  });

  it("marks a transfer with a small ⇄ before its category, but not what's ignored", () => {
    categories.list.push(category("Transfer", { is_transfer: 1 }), category("Ignore", { is_transfer: 1 }));
    const { unmount } = render(TxRow, props(tx({ category: "Transfer" })));
    expect(within(row()).getByLabelText("Transfer")).toBeInTheDocument();
    unmount();
    render(TxRow, props(tx({ category: "Ignore" })));
    expect(within(row()).queryByLabelText("Transfer")).not.toBeInTheDocument();
  });

  it("shows the saved category again when a pick couldn't be saved", async () => {
    render(TxRow, props(tx(), { onsave: vi.fn().mockResolvedValue(false) }));
    const select = screen.getByRole("combobox", { name: "Category for Blue Bottle" });
    await pickCategory(select, "Groceries");
    await vi.waitFor(() => expect(pickedValue(select)).toBe("Coffee"));
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
      expect(screen.getByText("Pending")).toBeInTheDocument();
    });

    it("flags what needs review, except on the Review tab where everything does", () => {
      const { unmount } = render(TxRow, props(tx({ needs_review: 1 })));
      expect(screen.getByText("Review")).toBeInTheDocument();
      unmount();
      render(TxRow, props(tx({ needs_review: 1 }), { review: true }));
      expect(screen.queryByText("Review")).not.toBeInTheDocument();
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
      await pickCategory(screen.getByRole("combobox", { name: "Category for Blue Bottle" }), "Groceries");
      expect(p.onsave).toHaveBeenCalledWith("Groceries");
    });

    it("doesn't save when the blank option is picked", async () => {
      const p = props(tx());
      render(TxRow, p);
      await pickCategory(screen.getByRole("combobox", { name: "Category for Blue Bottle" }), "");
      expect(p.onsave).not.toHaveBeenCalled();
    });

    it("shows the AI's confidence and lets you accept its suggestion, with a ✓ and (phones) the word", async () => {
      const onaccept = vi.fn();
      const p = props(tx({ needs_review: 1, category_source: "ai", confidence: 0.87 }), { onaccept });
      render(TxRow, p);
      expect(screen.getByText("87%")).toBeInTheDocument();
      const check = screen.getByRole("button", { name: "Accept Coffee for Blue Bottle" });
      expect(check).toHaveClass("lg:size-9", "max-md:hidden");
      expect(check).toHaveAttribute("title", "Accept Coffee · set by the AI (87%)");
      await userEvent.click(check);
      await userEvent.click(screen.getByRole("button", { name: "Accept" }));
      expect(onaccept).toHaveBeenCalledTimes(2);
      expect(screen.getByRole("button", { name: "Accept" })).toHaveClass("md:hidden");
      expect(p.onsave).not.toHaveBeenCalled();
    });

    it("offers Accept for whatever set it: a rule or past choices as well as the AI", () => {
      const { unmount } = render(TxRow, props(tx({ needs_review: 1, category_source: "rule" }), { onaccept: vi.fn() }));
      expect(screen.getByRole("button", { name: "Accept Coffee for Blue Bottle" })).toHaveAttribute("title", "Accept Coffee · set by a rule");
      unmount();
      render(TxRow, props(tx({ needs_review: 1, category_source: "history" }), { onaccept: vi.fn() }));
      expect(screen.getByRole("button", { name: "Accept Coffee for Blue Bottle" })).toHaveAttribute("title", "Accept Coffee · set by your past choices");
    });

    it("has nothing to accept without a category, or once reviewed", () => {
      const { unmount } = render(TxRow, props(tx({ needs_review: 1, category: null }), { onaccept: vi.fn() }));
      expect(screen.queryByRole("button", { name: /^Accept/ })).not.toBeInTheDocument();
      unmount();
      render(TxRow, props(tx({ needs_review: 0 }), { onaccept: vi.fn() }));
      expect(screen.queryByRole("button", { name: /^Accept/ })).not.toBeInTheDocument();
    });

    it("keeps a category picked again on a row waiting for review (that accepts it)", async () => {
      const p = props(tx({ needs_review: 1, category_source: "ai" }));
      render(TxRow, p);
      await pickCategory(screen.getByRole("combobox", { name: "Category for Blue Bottle" }), "Coffee");
      expect(p.onsave).toHaveBeenCalledWith("Coffee");
    });

    it("tells a screen reader which cell is the account and which the amount", () => {
      render(TxRow, props(tx()));
      expect(within(row()).getByText("Account:")).toHaveClass("sr-only");
      expect(within(row()).getByText("Amount:")).toHaveClass("sr-only");
    });
  });

  describe("split", () => {
    const split = tx({ is_split: 1, category: null, amount: -30, splits: [{ category: "Groceries", amount: -20 }, { category: "Coffee", amount: -10, note: "beans" }] });

    it("lists each part's category and amount instead of a single category, without a split badge", () => {
      render(TxRow, props(split));
      expect(screen.queryByText("split")).not.toBeInTheDocument();
      expect(screen.getByText("2 parts").closest("span")).toHaveClass("lg:hidden");
      expect(screen.getByText("Groceries $20.00").parentElement).toHaveClass("max-lg:hidden");
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
    it("in Select mode, a tap anywhere on the row ticks it instead of opening anything", async () => {
      const onopen = vi.fn();
      const p = props(tx(), { selecting: true, onopen });
      render(TxRow, p);
      await userEvent.click(within(row()).getByRole("button", { name: "Blue Bottle" }));
      expect(p.onselect).toHaveBeenCalledWith(expect.any(MouseEvent), true);
      expect(onopen).not.toHaveBeenCalled();
      expect(screen.getByRole("checkbox")).toHaveClass("max-md:size-5");
    });

    it("rings the row the keyboard is on", () => {
      render(TxRow, props(tx(), { focused: true }));
      expect(row()).toHaveClass("ring-2");
      expect(row()).toHaveAttribute("data-focused");
    });

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
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    });
  });

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
      for (const word of ["Pending", "Review"]) {
        const badge = within(row()).getByTitle(word);
        expect(within(badge).getByLabelText(word)).toHaveClass("@sm/title:hidden");
        expect(within(badge).getByText(word)).toHaveClass("hidden", "@sm/title:inline");
        expect(badge).toHaveClass("shrink-0");
      }
    });

    it("lets the account and the bank's text share a line, hiding the text until its cell has room", () => {
      render(TxRow, props(tx({ account_name: "Shared Checking" })));
      const account = within(row()).getAllByText("Shared Checking").map((e) => e.parentElement!.parentElement!).find((e) => e.classList.contains("shrink-[4]"))!;
      expect(account).toHaveClass("min-w-0", "shrink-[4]");
      const bankText = within(row()).getByTitle("BLUE BOTTLE #123");
      expect(bankText).toHaveClass("hidden", "min-w-0", "truncate", "@sm/acct:block");
      expect(bankText.parentElement).toHaveClass("@container/acct", "min-w-0");
    });

    it("shows the account as a small badge on the merchant's logo, at every width", () => {
      app.state = { connected: true, brands: { a1: { institution: "SimpleFIN Bridge", initial: "S" } } };
      render(TxRow, props(tx({ account_name: "Shared Checking" })));
      const badge = row().querySelector("[data-account-badge]")!;
      expect(badge).toHaveClass("absolute");
      expect(badge).not.toHaveClass("md:hidden");
      expect(badge.className).not.toMatch(/\bring-/);
      expect(badge).toHaveAttribute("title", "Shared Checking");
      expect(badge).not.toHaveTextContent("Shared Checking");
      expect(within(badge as HTMLElement).getByText("S")).toHaveClass("size-4", "lg:size-3");
      expect(within(row()).getAllByText("S").filter((e) => !badge.contains(e)).every((e) => e.classList.contains("hidden"))).toBe(true);
    });

    it("has no badge when the list is filtered to one account", () => {
      render(TxRow, props(tx(), { oneAccount: true }));
      expect(row().querySelector("[data-account-badge]")).toBeNull();
    });

    it("shows the category as words, without its emoji", () => {
      render(TxRow, props(tx()));
      expect(within(row()).queryByText("☕")).not.toBeInTheDocument();
      expect(within(row()).getByText("Coffee")).toBeInTheDocument();
    });

    it("on a touch screen, shows the repeat icon only once it's linked (linking is in the sheet)", () => {
      render(TxRow, props(tx()));
      expect(within(row()).getByRole("button", { name: "Link to a recurring item" })).toHaveClass("[@media(hover:none)]:hidden");
    });

    it("hides row actions until hover only where the device can hover, so a touch screen always shows them", () => {
      render(TxRow, props(tx()));
      const split = within(row()).getByRole("button", { name: "Split" });
      expect(split).toHaveClass("hoverable:opacity-0", "hoverable:group-hover:opacity-100", "hoverable:group-focus-within:opacity-100");
      expect(split).not.toHaveClass("opacity-0");
      expect(screen.getByRole("checkbox", { name: "Select Blue Bottle" }).closest("label")).toHaveClass("hoverable:md:opacity-0");
    });
  });
});
