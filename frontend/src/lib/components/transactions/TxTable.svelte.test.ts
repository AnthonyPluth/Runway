// @vitest-environment jsdom
import { fireEvent, render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { category, tx } from "../../../test/fixtures";
import TxTable from "./TxTable.svelte";
import { viewport } from "$lib/phone.svelte";
import { pickCategory, pickedValue } from "../../../test/pick";

const items = [
  tx({ id: "a", posted: "2026-03-10", amount: -10, payee: "Alpha" }),
  tx({ id: "b", posted: "2026-03-10", amount: -5, payee: "Bravo" }),
  tx({ id: "c", posted: "2026-03-09", amount: 100, payee: "Charlie" }),
  tx({ id: "d", posted: "2025-12-31", amount: -1, payee: "Delta" }),
];
const setup = (extra: Record<string, unknown> = {}) => {
  const p = { items, total: items.length, review: false, recurring: [], onsave: vi.fn(), onchanged: vi.fn(), ...extra };
  render(TxTable, p);
  return p;
};
const tick = (name: string) => screen.getByRole("checkbox", { name: `Select ${name}` });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-06-01T12:00:00") });
  vi.mocked(api).mockReset();
  categories.list = [category("Coffee"), category("Groceries")];
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); viewport.phone = false; });

describe("TxTable", () => {
  it("groups by day, newest first, with each day's net", () => {
    setup();
    const days = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent!.replace(/\u00a0/g, " "));
    expect(days).toEqual(["Tuesday, Mar 10 −$15.00", "Monday, Mar 9 +$100.00", "Wed, Dec 31, 2025 −$1.00"]);
  });

  it("leaves out a day's net when it comes to nothing", () => {
    setup({ items: [tx({ id: "x", amount: -5 }), tx({ id: "y", amount: 5 })], total: 2 });
    expect(screen.getByRole("heading", { level: 3 })).not.toHaveTextContent("$");
  });

  it("leaves transfers and card payments out of a day's net, and the transfer part of a split", () => {
    categories.list = [category("Groceries"), { ...category("Transfer"), is_transfer: 1 }];
    setup({ items: [
      tx({ id: "p", amount: 3000, payee: "Paycheck", category: "Income" }),
      tx({ id: "t", amount: -500, payee: "To savings", category: "Transfer" }),
      tx({ id: "u", amount: 500, payee: "From checking", category: "Transfer" }),
      tx({ id: "s", amount: -100, payee: "Split", category: "Groceries", is_split: 1, splits: [{ category: "Groceries", amount: -60 }, { category: "Transfer", amount: -40 }] }),
    ], total: 4 });
    expect(screen.getByRole("heading", { level: 3 }).textContent!.replace(/\u00a0/g, " ")).toMatch(/\$2,940\.00$/);
    expect(screen.getByText("+$2,940.00")).toHaveAttribute("title", "Transfers not counted");
  });

  it("says nothing about transfers on a day without one", () => {
    setup();
    expect(screen.getByText("−$15.00")).not.toHaveAttribute("title");
  });

  it("counts only the matching part of a split one under a category filter", () => {
    setup({ only: "Groceries", items: [
      tx({ id: "s", amount: -100, payee: "Split", is_split: 1, splits: [{ category: "Groceries", amount: -60 }, { category: "Shopping", amount: -40 }],
        match: { amount: -60, categories: ["Groceries"] } }),
    ], total: 1 });
    expect(screen.getByRole("heading", { level: 3 }).textContent!.replace(/\u00a0/g, " ")).toMatch(/−\$60\.00$/);
  });

  it("shows no count of its own, which the page heading and Show more already give", () => {
    setup({ total: 40 });
    expect(screen.queryByText("4 of 40")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show more (36 left)" })).toBeInTheDocument();
  });

  describe("selecting", () => {
    it("shows a bar with the count and total of what's ticked", async () => {
      setup();
      expect(screen.queryByRole("region", { name: "Change the selected transactions" })).not.toBeInTheDocument();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(tick("Charlie"));
      const bar = screen.getByRole("region", { name: "Change the selected transactions" });
      expect(bar).toHaveTextContent("2 selected");
      expect(bar).toHaveTextContent("+$90.00");
    });

    it("selects the whole range on shift-click", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await fireEvent.click(tick("Charlie"), { shiftKey: true });
      expect(screen.getByText("3 selected")).toBeInTheDocument();
      expect(tick("Bravo")).toBeChecked();
    });

    it("selects and clears everything with the header checkbox and Clear selection", async () => {
      setup();
      await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
      expect(screen.getByText("4 selected")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));
      expect(screen.queryByText("4 selected")).not.toBeInTheDocument();
    });

    it("shows the header box as partly ticked when only some are", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      expect(screen.getByRole("checkbox", { name: "Select all shown" })).toBePartiallyChecked();
    });
  });

  describe("changing many at once", () => {
    const was = [{ id: "a", category: "Coffee", category_source: "ai", confidence: 0.8, needs_review: 1, payee: "Alpha", is_split: 0 }];
    beforeEach(() => { vi.mocked(api).mockResolvedValue({ updated: 2, was }); });
    const undoToast = () => vi.mocked(toast).mock.calls.at(-1) as [string, { action: { label: string; onClick: () => Promise<void> } }];

    it("sets a category for the selected transactions", async () => {
      const p = setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(tick("Bravo"));
      await pickCategory(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a", "b"], category: "Groceries" } });
      expect(undoToast()[0]).toBe("Set to Groceries · 2 transactions");
      expect(undoToast()[1].action.label).toBe("Undo");
      expect(p.onchanged).toHaveBeenCalled();
    });

    it("offers to use the category for the merchant from now on when they're all one merchant's", async () => {
      vi.mocked(api).mockResolvedValue({ updated: 2, was, offer_rule: { merchant: "Alpha", match: "alpha", also_updated: 3 } });
      setup();
      await userEvent.click(tick("Alpha"));
      await pickCategory(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
      const [msg, opts] = vi.mocked(toast).mock.calls.at(-1) as unknown as [string, { description: string; action: { label: string }; cancel: { label: string } }];
      expect(msg).toBe("Set to Groceries · 2 transactions");
      expect(opts.action.label).toBe("Always for Alpha");
      expect(opts.cancel.label).toBe("Undo");
      expect(opts.description).toBe("matches “alpha” · +3 more");
    });

    it("under a category filter, sends it along so a split one changes only that part", async () => {
      setup({ only: "Coffee" });
      await userEvent.click(tick("Alpha"));
      await pickCategory(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], category: "Groceries", only: "Coffee" } });
    });

    it("empties the category and name boxes and the selection once it's done", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.type(screen.getByRole("textbox", { name: "New merchant name" }), "Acme");
      await pickCategory(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
      expect(screen.queryByRole("region", { name: "Change the selected transactions" })).not.toBeInTheDocument();
      await userEvent.click(tick("Bravo"));
      expect(screen.getByRole("textbox", { name: "New merchant name" })).toHaveValue("");
      expect(pickedValue(screen.getByRole("combobox", { name: "Category for the selected transactions" }))).toBe("");
    });

    it("undoes with what each transaction was, which the server sent back", async () => {
      const p = setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Accept" }));
      vi.mocked(api).mockClear();
      await undoToast()[1].action.onClick();
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { restore: was } });
      expect(toast).toHaveBeenLastCalledWith("Undone", undefined);
      expect(p.onchanged).toHaveBeenCalledTimes(2);
    });

    it("says so when the undo fails", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Accept" }));
      vi.mocked(api).mockRejectedValue(new Error("Gone"));
      await undoToast()[1].action.onClick();
      expect(toast.error).toHaveBeenCalledWith("Gone");
    });

    it("marks the bar as an editor while something is selected, so a background reload leaves it alone", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      expect(screen.getByRole("region", { name: "Change the selected transactions" })).toHaveAttribute("data-editor");
    });

    describe("asking first", () => {
      const many = Array.from({ length: 12 }, (_, i) => tx({ id: `m${i}`, posted: "2026-03-10", amount: -1, payee: `Merchant ${i}` }));
      const pickAll = async (n: number) => {
        setup({ items: many, total: many.length });
        for (let i = 0; i < n; i++) await userEvent.click(tick(`Merchant ${i}`));
      };

      it("asks with the count at ten or more, and only then changes them", async () => {
        await pickAll(10);
        await pickCategory(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
        const dialog = await screen.findByRole("dialog", { name: "Set Groceries on 10 transactions?" });
        expect(api).not.toHaveBeenCalled();
        await userEvent.click(within(dialog).getByRole("button", { name: "Set category" }));
        await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/bulk",
          { method: "POST", body: { ids: many.slice(0, 10).map((t) => t.id), category: "Groceries" } }));
        expect(undoToast()[0]).toBe("Set to Groceries · 2 transactions");
      });

      it("leaves everything as it was when you cancel", async () => {
        await pickAll(11);
        await pickCategory(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
        await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
        expect(api).not.toHaveBeenCalled();
        expect(screen.getByText("11 selected")).toBeInTheDocument();
        expect(pickedValue(screen.getByRole("combobox", { name: "Category for the selected transactions" }))).toBe("");
      });

      it("asks before a rename and before accepting", async () => {
        await pickAll(12);
        await userEvent.type(screen.getByRole("textbox", { name: "New merchant name" }), "Acme");
        await userEvent.click(screen.getByRole("button", { name: "Rename" }));
        await userEvent.click(within(await screen.findByRole("dialog", { name: "Rename 12 transactions to Acme?" })).getByRole("button", { name: "Cancel" }));
        await userEvent.click(screen.getByRole("button", { name: "Accept" }));
        expect(await screen.findByRole("dialog", { name: "Accept 12 transactions?" })).toBeInTheDocument();
        expect(api).not.toHaveBeenCalled();
      });

      it("doesn't ask for fewer than ten", async () => {
        await pickAll(9);
        await userEvent.click(screen.getByRole("button", { name: "Accept" }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/bulk", expect.objectContaining({ method: "POST" })));
      });
    });

    it("renames the merchant on Enter or with the button, but not to nothing", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Rename" }));
      expect(api).not.toHaveBeenCalled();
      await userEvent.type(screen.getByRole("textbox", { name: "New merchant name" }), "  Acme {Enter}");
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], payee: "Acme" } });
      expect(undoToast()[0]).toBe("Renamed to Acme · 2 transactions");
    });

    it("accepts their categories", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Accept" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], reviewed: true } });
    });

    it("shows the error and doesn't reload the list when it fails", async () => {
      vi.mocked(api).mockRejectedValue(new Error("Locked"));
      const p = setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Accept" }));
      expect(toast.error).toHaveBeenCalledWith("Locked");
      expect(p.onchanged).not.toHaveBeenCalled();
    });
  });

  describe("paging", () => {
    it("loads more by hand and disables the button meanwhile", async () => {
      let done!: () => void;
      const onmore = vi.fn(() => new Promise<void>((r) => { done = r; }));
      setup({ total: 10, onmore });
      await userEvent.click(screen.getByRole("button", { name: "Show more (6 left)" }));
      expect(onmore).toHaveBeenCalledOnce();
      expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
      done();
      expect(await screen.findByRole("button", { name: /Show more/ })).toBeEnabled();
    });

    it("says when more couldn't be loaded, and tries again only when asked", async () => {
      const onmore = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(undefined);
      setup({ total: 10, onmore });
      await userEvent.click(screen.getByRole("button", { name: "Show more (6 left)" }));
      expect(await screen.findByText(/Couldn’t load more/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Show more/ })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(onmore).toHaveBeenCalledTimes(2);
      expect(await screen.findByRole("button", { name: /Show more/ })).toBeEnabled();
    });

    it("has no Show more once everything is loaded", () => {
      setup();
      expect(screen.queryByRole("button", { name: /Show more/ })).not.toBeInTheDocument();
    });
  });

  describe("select all", () => {
    const every = { q: "coffee", ignored: "0" };
    it("offers every one the filters match once all the loaded ones are ticked, and changes them by the filters", async () => {
      vi.mocked(api).mockResolvedValue({ updated: 212, was: [] });
      const p = setup({ total: 212, every, onmore: vi.fn() });
      expect(screen.queryByRole("button", { name: /Select all 212/ })).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
      await userEvent.click(screen.getByRole("button", { name: "Select all 212" }));
      expect(screen.getByText("212 selected")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Accept" }));
      expect(screen.getByRole("dialog")).toHaveTextContent("Accept 212 transactions?");
      await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Accept" }));
      await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { filter: every, reviewed: true } }));
      expect(p.onchanged).toHaveBeenCalled();
    });

    it("goes back to the ticked ones when one is unticked", async () => {
      setup({ total: 212, every });
      await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
      await userEvent.click(screen.getByRole("button", { name: "Select all 212" }));
      await userEvent.click(tick("Alpha"));
      expect(screen.getByText("3 selected")).toBeInTheDocument();
    });

    it("isn't offered when everything is loaded", async () => {
      setup({ every });
      await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
      expect(screen.queryByRole("button", { name: /Select all/ })).not.toBeInTheDocument();
    });
  });

  it("hands a picked category to onsave along with the transaction", async () => {
    const p = setup();
    const row = screen.getAllByRole("listitem")[0];
    await pickCategory(within(row).getByRole("combobox"), "Groceries");
    expect(p.onsave).toHaveBeenCalledWith(items[0], "Groceries");
  });

  describe("on a phone", () => {
    it("keeps the bar to one line: how many, Categorize and More for the rest", async () => {
      vi.mocked(api).mockResolvedValue({ updated: 1, was: [] });
      viewport.phone = true;
      const p = setup({ selecting: true });
      await userEvent.click(tick("Alpha"));
      const bar = screen.getByRole("region", { name: "Change the selected transactions" });
      expect(bar).toHaveTextContent("1 selected");
      expect(within(bar).getByRole("combobox", { name: "Category for the selected transactions" })).toHaveTextContent("Categorize");
      expect(within(bar).queryByRole("textbox")).not.toBeInTheDocument();
      await userEvent.click(within(bar).getByRole("button", { name: "More actions" }));
      await fireEvent.click(screen.getByText("Accept", { selector: "[data-popover-content] button" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], reviewed: true } });
      expect(p.onchanged).toHaveBeenCalled();
    });

    it("in Select mode, ticks a row from a tap anywhere on it", async () => {
      viewport.phone = true;
      setup({ selecting: true });
      await userEvent.click(screen.getByText("Bravo"));
      expect(tick("Bravo")).toBeChecked();
    });
  });
});
