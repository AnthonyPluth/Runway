// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn().mockResolvedValue({}), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { txFilters } from "$lib/filters.svelte";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import BudgetRow from "./BudgetRow.svelte";
import type { BudgetCategory } from "./types";

const cat = (extra: Partial<BudgetCategory> = {}): BudgetCategory => ({
  name: "Groceries", parent: null, path: ["Groceries"], depth: 0, top: "Groceries", has_children: false, budget: 500, pay_with: null,
  usual_account: null, spent: 200, own_spent: 200, left: 300, ...extra,
});
const pay = [{ id: "c1", name: "Visa", kind: "credit" }, { id: "b1", name: "Checking", kind: "checking" }];
const setup = (c = cat(), extra: Record<string, unknown> = {}) => {
  const cbs = { onsave: vi.fn(), onchanged: vi.fn() };
  render(BudgetRow, { c, month: "2026-03", pace: 0.5, payAccounts: pay, ...cbs, ...extra });
  return cbs;
};
const bar = () => screen.getByRole("img", { name: /of budget used/ });

beforeEach(() => { categories.list = [category("Groceries")]; vi.mocked(api).mockClear(); vi.mocked(toast.error).mockClear(); });

describe("BudgetRow", () => {
  it("shows what's spent of the budget and what's left", () => {
    setup();
    expect(screen.getByText("$200")).toBeInTheDocument();
    // only the name opens Transactions; the amount is plain text
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByLabelText("Budget for Groceries")).toHaveValue("500");
    expect(screen.getByText("$300 left")).toBeInTheDocument();
    expect(bar()).toHaveAttribute("aria-label", "40% of budget used");
  });

  describe("a subcategory", () => {
    const produce = (extra: Partial<BudgetCategory> = {}) =>
      cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, top: "Groceries", budget: 100, spent: 40, left: 60, ...extra });

    it("has its figures but no bar or pace mark (only its parent has those)", () => {
      setup(produce(), { sub: true, budgets: true });
      expect(screen.getByText("$40")).toBeInTheDocument();
      expect(screen.getByLabelText("Budget for Produce")).toHaveValue("100");
      expect(screen.queryByRole("img", { name: /of budget used/ })).not.toBeInTheDocument();
      expect(screen.queryByTitle("Where you'd be at an even pace today")).not.toBeInTheDocument();
      expect(screen.queryByText(/left/)).not.toBeInTheDocument();
    });

    it("shows it's over in red, without the bar", () => {
      setup(produce({ spent: 130, left: -30 }), { sub: true, budgets: true });
      const spent = screen.getByTitle("$30 over");
      expect(spent).toHaveClass("text-destructive");
      expect(spent).toHaveTextContent("$130 ($30 over)");
    });
  });

  describe("the account it's paid with", () => {
    beforeEach(() => { app.state = { connected: true, brands: { c1: { institution: "Big Bank", src: "/logo/c1.png", initial: "B" }, b1: { institution: "Credit Union", src: null, initial: "C" } } }; });
    afterEach(() => { app.state = null; });
    const badge = (container: HTMLElement) => container.querySelector("[data-account-badge]");

    it("sits on the emoji as its bank's logo, named in its title", () => {
      const { container } = render(BudgetRow, { c: cat(), month: "2026-03", pace: 0.5, payAccounts: pay, account: "c1", onsave: vi.fn(), onchanged: vi.fn() });
      expect(badge(container)).toHaveAttribute("title", "Visa");
      expect(badge(container)!.querySelector("img")).toHaveAttribute("src", "/logo/c1.png");
      expect(badge(container)).not.toHaveClass("ring-2");
    });

    it("is the bank's letter without a logo, on a subcategory or a row without a budget too", () => {
      const { container } = render(BudgetRow, { c: cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, budget: null, left: null }),
        sub: true, month: "2026-03", pace: 0.5, payAccounts: pay, account: "b1", onsave: vi.fn(), onchanged: vi.fn() });
      expect(badge(container)).toHaveAttribute("title", "Checking");
      expect(screen.getByRole("img", { name: "Checking" })).toHaveTextContent("C");
    });

    it("isn't there when no account applies", () => {
      setup();
      expect(badge(document.body)).toBeNull();
    });
  });

  it("shows a subcategory's emoji too, a little smaller", () => {
    categories.list = [category("Groceries", { icon: "🛒" }), category("Produce", { icon: "🥦", parent: "Groceries" })];
    const { container } = render(BudgetRow, { c: cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1 }), sub: true,
      month: "2026-03", pace: 0.5, payAccounts: pay, onsave: vi.fn(), onchanged: vi.fn() });
    const icon = [...container.querySelectorAll("span[aria-hidden]")].find((el) => el.textContent === "🥦");
    expect(icon).toHaveStyle({ width: "20px" });
  });

  it("turns red and says how much it's over when spending passes the budget", () => {
    setup(cat({ spent: 620, left: -120 }));
    expect(screen.getByText("▲ $120 over")).toHaveClass("text-destructive");
    expect(bar().firstElementChild).toHaveClass("bg-destructive");
    expect(bar()).toHaveAttribute("aria-label", "124% of budget used");   // said as it is; the drawn bar stops at full
    expect(bar().firstElementChild).toHaveStyle({ width: "100%" });
  });

  it("warns when you're spending faster than the month is going", () => {
    setup(cat({ spent: 400, left: 100 }), { pace: 0.5 });
    expect(screen.getByText("$100 left · ahead of pace")).toBeInTheDocument();
  });

  it("shows where an even pace would be, but not for a finished or future month", () => {
    const { unmount } = render(BudgetRow, { c: cat(), month: "2026-03", pace: 0.5, payAccounts: pay, onsave: vi.fn(), onchanged: vi.fn() });
    expect(screen.getByTitle("Where you'd be at an even pace today")).toBeInTheDocument();
    unmount();
    render(BudgetRow, { c: cat(), month: "2026-03", pace: 1, payAccounts: pay, onsave: vi.fn(), onchanged: vi.fn() });
    expect(screen.queryByTitle("Where you'd be at an even pace today")).not.toBeInTheDocument();
  });

  it("includes what rolled over from earlier months", () => {
    setup(cat({ carried: 50, available: 550 }));
    expect(screen.getByText("$500 + $50 rolled over from earlier months")).toBeInTheDocument();
    expect(bar()).toHaveAttribute("aria-label", "36% of budget used");   // 200 of 550
  });

  it("says nothing about what's left when nothing is spent yet, and keeps cents a rounding would hide", () => {
    const { unmount } = render(BudgetRow, { c: cat({ spent: 0, left: 500 }), month: "2026-03", pace: 1, payAccounts: pay, onsave: vi.fn(), onchanged: vi.fn() });
    expect(screen.queryByText(/left/)).not.toBeInTheDocument();
    unmount();
    setup(cat({ spent: 500.3, left: -0.3 }));
    expect(screen.getByText("▲ $0.30 over")).toBeInTheDocument();
  });

  it("has no bar for a category without a budget, just a box to set one", () => {
    setup(cat({ budget: null, left: null }));
    expect(screen.queryByRole("img", { name: /of budget used/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Budget for Groceries")).toHaveAttribute("placeholder", "Budget");
  });

  it("saves the budget when the box changes", async () => {
    const { onsave } = setup();
    const box = screen.getByLabelText("Budget for Groceries");
    await userEvent.clear(box);
    await userEvent.type(box, "650");
    await userEvent.tab();
    expect(onsave).toHaveBeenCalledWith("Groceries", "650");
  });

  it("opens the transactions that add up to the spending", async () => {
    setup();
    location.hash = "";
    await userEvent.click(screen.getByRole("link", { name: "Groceries" }));
    expect(txFilters.transactions).toMatchObject({ category: "Groceries", from: "2026-03-01", to: "2026-03-31", scope: "budget" });
    expect(location.hash).toBe("#transactions?category=Groceries&from=2026-03-01&to=2026-03-31&scope=budget");
  });

  describe("in the Budgets card", () => {
    it("offers to roll over what's left, and says so once it does", async () => {
      const { onchanged } = setup(cat(), { budgets: true });
      await userEvent.click(screen.getByRole("button", { name: /Roll over/ }));
      expect(api).toHaveBeenCalledWith("/api/budget", { method: "POST", body: { category: "Groceries", rollover: true } });
      expect(toast.success).toHaveBeenCalledWith("Groceries rolls over from this month on");
      expect(onchanged).toHaveBeenCalled();
    });

    it("turns rollover off", async () => {
      setup(cat({ rollover_from: "2026-01" }), { budgets: true });
      const btn = screen.getByRole("button", { name: /Rolls over/ });
      expect(btn).toHaveAttribute("aria-pressed", "true");
      await userEvent.click(btn);
      expect(api).toHaveBeenCalledWith("/api/budget", { method: "POST", body: { category: "Groceries", rollover: false } });
    });

    it("doesn't offer rollover on a subcategory, nor a card anywhere (that's in Settings › Categories)", () => {
      setup(cat(), { sub: true, budgets: true });
      expect(screen.queryByRole("button", { name: /Roll over/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Set card/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });
  });
});
