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
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-06-01T12:00:00") });   // "this year" is 2026
  vi.mocked(api).mockReset();
  categories.list = [category("Coffee"), category("Groceries")];
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

describe("TxTable", () => {
  it("groups by day, newest first, with each day's net", () => {
    setup();
    const days = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent!.replace(/\u00a0/g, " "));
    expect(days).toEqual(["Tuesday, Mar 10 -$15.00", "Monday, Mar 9 $100.00", "Wed, Dec 31, 2025 -$1.00"]);
  });

  it("leaves out a day's net when it comes to nothing", () => {
    setup({ items: [tx({ id: "x", amount: -5 }), tx({ id: "y", amount: 5 })], total: 2 });
    expect(screen.getByRole("heading", { level: 3 })).not.toHaveTextContent("$");
  });

  it("says how many are shown of how many there are", () => {
    setup({ total: 40 });
    expect(screen.getByText("4 of 40")).toBeInTheDocument();
  });

  it("says just the count when everything is shown", () => {
    setup();
    expect(screen.getByText("4 transactions")).toBeInTheDocument();
  });

  describe("selecting", () => {
    it("shows a bar with the count and total of what's ticked", async () => {
      setup();
      expect(screen.queryByRole("region", { name: "Change the selected transactions" })).not.toBeInTheDocument();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(tick("Charlie"));
      const bar = screen.getByRole("region", { name: "Change the selected transactions" });
      expect(bar).toHaveTextContent("2 selected");
      expect(bar).toHaveTextContent("$90.00");
    });

    it("selects the whole range on shift-click", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await fireEvent.click(tick("Charlie"), { shiftKey: true });
      expect(screen.getByText("3 selected")).toBeInTheDocument();
      expect(tick("Bravo")).toBeChecked();
    });

    it("selects and clears everything with the header checkbox and Clear", async () => {
      setup();
      await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
      expect(screen.getByText("4 selected")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Clear" }));
      expect(screen.queryByText("4 selected")).not.toBeInTheDocument();
    });

    it("shows the header box as partly ticked when only some are", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      expect(screen.getByRole("checkbox", { name: "Select all shown" })).toBePartiallyChecked();
    });
  });

  describe("changing many at once", () => {
    beforeEach(() => { vi.mocked(api).mockResolvedValue({ updated: 2 }); });   // (braces: see pickers test)

    it("sets a category for the selected transactions", async () => {
      const p = setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(tick("Bravo"));
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category for the selected transactions" }), "Groceries");
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a", "b"], category: "Groceries" } });
      expect(toast.success).toHaveBeenCalledWith("Set to Groceries · 2 transactions");
      expect(p.onchanged).toHaveBeenCalled();
    });

    it("renames the merchant on Enter or with the button, but not to nothing", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Rename" }));
      expect(api).not.toHaveBeenCalled();
      await userEvent.type(screen.getByRole("textbox", { name: "New merchant name" }), "  Acme {Enter}");
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], payee: "Acme" } });
      expect(toast.success).toHaveBeenCalledWith("Renamed to Acme · 2 transactions");
    });

    it("marks them reviewed", async () => {
      setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
      expect(api).toHaveBeenCalledWith("/api/transactions/bulk", { method: "POST", body: { ids: ["a"], reviewed: true } });
    });

    it("shows the error and doesn't reload the list when it fails", async () => {
      vi.mocked(api).mockRejectedValue(new Error("Locked"));
      const p = setup();
      await userEvent.click(tick("Alpha"));
      await userEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
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

    it("has no Show more once everything is loaded", () => {
      setup();
      expect(screen.queryByRole("button", { name: /Show more/ })).not.toBeInTheDocument();
    });
  });

  it("hands a picked category to onsave along with the transaction", async () => {
    const p = setup();
    const row = screen.getAllByRole("listitem")[0];
    await userEvent.selectOptions(within(row).getByRole("combobox"), "Groceries");
    expect(p.onsave).toHaveBeenCalledWith(items[0], "Groceries");
  });
});
