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
import { viewport } from "$lib/phone.svelte";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import BudgetRow from "./BudgetRow.svelte";
import type { BudgetCategory } from "./types";

const cat = (extra: Partial<BudgetCategory> = {}): BudgetCategory => ({
  name: "Groceries", parent: null, path: ["Groceries"], depth: 0, top: "Groceries", has_children: false, budget: 500, pay_with: null,
  usual_account: null, rollover_from: null, carried: 0, available: null, spent: 200, own_spent: 200, left: 300, expected: 0, usual_budget: null, month_budget: null, ...extra,
});
const pay = [{ id: "c1", name: "Visa", kind: "credit" }, { id: "b1", name: "Checking", kind: "checking" }];
const setup = (c = cat(), extra: Record<string, unknown> = {}) => {
  const cbs = { onchanged: vi.fn() };
  render(BudgetRow, { c, month: "2026-03", pace: 0.5, payAccounts: pay, ...cbs, ...extra });
  return cbs;
};
const bar = () => screen.getByRole("img", { name: /of budget used/ });
const field = (name = "Groceries") => screen.getByRole("button", { name: new RegExp(`^Edit ${name} budget`) });

beforeEach(() => { categories.list = [category("Groceries")]; vi.mocked(api).mockReset().mockResolvedValue({} as never); vi.mocked(toast.error).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast).mockClear(); });

describe("BudgetRow", () => {
  it("shows what's spent of the budget and what's left", () => {
    setup();
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["Groceries", "$200"]);
    expect(field()).toHaveTextContent("$500");
    expect(screen.getByText("$300 left")).toBeInTheDocument();
    expect(bar()).toHaveAttribute("aria-label", "40% of budget used");
  });

  it("shows the amount as a field-like button with a pencil, named with its amount and scope", () => {
    setup(cat(), { budgets: true });
    expect(field()).toHaveAccessibleName("Edit Groceries budget: $500, usual monthly amount");
    expect(field()).toHaveClass("border", "border-input", "cursor-pointer");
    expect(field().querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();   // nothing to type into until it's opened
  });

  it("tightens the amount group on a phone only, leaving the other widths alone", () => {
    setup(cat({ budget: 12000 }), { budgets: true });
    expect(field()).toHaveClass("h-9", "sm:h-7", "min-w-[5.25rem]", "sm:min-w-24");
    expect(field()).toHaveTextContent("$12,000");
    const group = field().closest("span.ml-auto")!;
    expect(group).toHaveClass("gap-1.5", "phone:gap-1");
    expect(group.parentElement).toHaveClass("gap-x-2.5", "phone:gap-x-1.5");
    expect(group.parentElement!.className).not.toMatch(/nowrap/);
  });

  describe("a subcategory", () => {
    const produce = (extra: Partial<BudgetCategory> = {}) =>
      cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, top: "Groceries", budget: 100, spent: 40, left: 60, ...extra });

    it("has its figures but no bar or pace mark (only its parent has those)", () => {
      setup(produce(), { sub: true, budgets: true });
      expect(screen.getByText("$40")).toBeInTheDocument();
      expect(field("Produce")).toHaveTextContent("$100");
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

    it("sits on the emoji as its bank's logo (the shared badge, sized to the emoji), named in its title", () => {
      const { container } = render(BudgetRow, { c: cat(), month: "2026-03", pace: 0.5, payAccounts: pay, account: "c1", onchanged: vi.fn() });
      expect(badge(container)).toHaveAttribute("title", "Visa");
      expect(badge(container)).toHaveClass("pointer-events-auto");
      expect(badge(container)).toHaveClass("phone:hidden");
      expect(badge(container)!.querySelector("img")).toHaveAttribute("src", "/logo/c1.png");
      expect(badge(container)!.querySelector("img")!.parentElement).toHaveClass("size-4");
    });

    it("is the bank's letter without a logo, smaller on a subcategory, and on a row without a budget too", () => {
      const { container } = render(BudgetRow, { c: cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, budget: null, left: null }),
        sub: true, month: "2026-03", pace: 0.5, payAccounts: pay, account: "b1", onchanged: vi.fn() });
      expect(badge(container)).toHaveAttribute("title", "Checking");
      expect(badge(container)).toHaveTextContent("C");
      expect(badge(container)!.firstElementChild).toHaveClass("size-3");
    });

    it("isn't there when no account applies", () => {
      setup();
      expect(badge(document.body)).toBeNull();
    });
  });

  it("shows a subcategory's emoji too, a little smaller", () => {
    categories.list = [category("Groceries", { icon: "🛒" }), category("Produce", { icon: "🥦", parent: "Groceries" })];
    const { container } = render(BudgetRow, { c: cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1 }), sub: true,
      month: "2026-03", pace: 0.5, payAccounts: pay, onchanged: vi.fn() });
    const icon = [...container.querySelectorAll("span[aria-hidden]")].find((el) => el.textContent === "🥦");
    expect(icon).toHaveStyle({ width: "20px" });
  });

  it("turns red and says how much it's over when spending passes the budget", () => {
    setup(cat({ spent: 620, left: -120 }));
    expect(screen.getByText("▲ $120 over")).toHaveClass("text-destructive");
    expect(bar().firstElementChild).toHaveClass("bg-destructive");
    expect(bar()).toHaveAttribute("aria-label", "124% of budget used");
    expect(bar().firstElementChild).toHaveStyle({ width: "100%" });
  });

  it("warns when you're spending faster than the month is going", () => {
    setup(cat({ spent: 400, left: 100 }), { pace: 0.5 });
    expect(screen.getByText("$100 left · ahead of pace")).toBeInTheDocument();
  });

  it("shows where an even pace would be, but not for a finished or future month", () => {
    const { unmount } = render(BudgetRow, { c: cat(), month: "2026-03", pace: 0.5, payAccounts: pay, onchanged: vi.fn() });
    expect(screen.getByTitle("Where you'd be at an even pace today")).toBeInTheDocument();
    unmount();
    render(BudgetRow, { c: cat(), month: "2026-03", pace: 1, payAccounts: pay, onchanged: vi.fn() });
    expect(screen.queryByTitle("Where you'd be at an even pace today")).not.toBeInTheDocument();
  });

  it("includes what rolled over from earlier months", () => {
    setup(cat({ carried: 50, available: 550 }));
    expect(screen.getByText("$500 + $50 rolled over from earlier months")).toBeInTheDocument();
    expect(bar()).toHaveAttribute("aria-label", "36% of budget used");
  });

  it("says nothing about what's left when nothing is spent yet, and keeps cents a rounding would hide", () => {
    const { unmount } = render(BudgetRow, { c: cat({ spent: 0, left: 500 }), month: "2026-03", pace: 1, payAccounts: pay, onchanged: vi.fn() });
    expect(screen.queryByText(/left/)).not.toBeInTheDocument();
    unmount();
    setup(cat({ spent: 500.3, left: -0.3 }));
    expect(screen.getByText("▲ $0.30 over")).toBeInTheDocument();
  });

  it("has no bar for a category without a budget, just a field to set one", () => {
    setup(cat({ budget: null, left: null }));
    expect(screen.queryByRole("img", { name: /of budget used/ })).not.toBeInTheDocument();
    expect(field()).toHaveTextContent("Budget");
    expect(field()).toHaveAccessibleName("Edit Groceries budget: not set");
  });

  it.each(["Groceries", "$200"])("opens the transactions that add up to the spending from %s", async (name) => {
    setup();
    location.hash = "";
    await userEvent.click(screen.getByRole("link", { name }));
    expect(txFilters.transactions).toMatchObject({ category: "Groceries", from: "2026-03-01", to: "2026-03-31", scope: "budget" });
    expect(location.hash).toBe("#transactions?category=Groceries&from=2026-03-01&to=2026-03-31&scope=budget");
  });

  describe("what's still coming this month", () => {
    const expectedBar = () => bar().querySelector<HTMLElement>("[data-expected]");

    it("is the lighter part of the bar after what's spent, and what's left after it", () => {
      setup(cat({ expected: 100 }));
      expect(screen.getByText("$200 left · $100 coming")).toBeInTheDocument();
      expect(expectedBar()).toHaveClass("opacity-45");
      expect(expectedBar()).not.toHaveClass("bg-destructive");
      expect(expectedBar()).toHaveStyle({ width: "60%" });
      expect(bar()).toHaveAttribute("aria-label", "40% of budget used, 20% more coming");
    });

    it("warns, without shouting, when it would take the budget over", () => {
      setup(cat({ spent: 450, left: 50, expected: 80 }));
      const note = screen.getByText("▲ $30 over with what’s coming");
      expect(note).toHaveClass("text-destructive");
      expect(note).not.toHaveClass("font-semibold");
      expect(expectedBar()).toHaveClass("bg-destructive", "opacity-45");
      expect(expectedBar()).toHaveStyle({ width: "100%" });
      const spentBar = bar().querySelector(":scope > div:not([data-expected])");
      expect(spentBar).toHaveStyle({ width: "90%" });
      expect(spentBar).not.toHaveClass("bg-destructive");
    });

    it("leaves nothing left rather than a negative amount when it fills the budget exactly", () => {
      setup(cat({ spent: 400, left: 100, expected: 100 }));
      expect(screen.getByText("$0 left · $100 coming")).toBeInTheDocument();
    });

    it("isn't drawn once the spending alone is over", () => {
      setup(cat({ spent: 620, left: -120, expected: 50 }));
      expect(screen.getByText("▲ $120 over")).toHaveClass("font-semibold");
      expect(expectedBar()).toBeNull();
    });

    it("isn't there without anything coming", () => {
      setup();
      expect(expectedBar()).toBeNull();
      expect(screen.queryByText(/coming/)).not.toBeInTheDocument();
    });

    it("marks a subcategory that would go over, more quietly than one that is", () => {
      setup(cat({ name: "Produce", parent: "Groceries", path: ["Groceries", "Produce"], depth: 1, budget: 100, spent: 70, left: 30, expected: 45 }),
        { sub: true, budgets: true });
      const spent = screen.getByTitle("$15 over with what’s coming");
      expect(spent).toHaveClass("text-destructive");
      expect(spent).not.toHaveClass("font-semibold");
      expect(spent).toHaveTextContent("$70 ($15 over with what’s coming)");
    });
  });

  describe("an income category", () => {
    const pay = (extra: Partial<BudgetCategory> = {}) => cat({ name: "Paycheck", path: ["Paycheck"], top: "Paycheck", budget: 6000, spent: 3000, left: 3000, ...extra });
    const incomeBar = () => screen.getByRole("img", { name: /of expected income received/ });

    it("fills a green bar with what's come in, and says what's still to come and how much is scheduled", () => {
      setup(pay({ expected: 3000 }), { income: true, budgets: true });
      expect(screen.getByText("$3,000 to come · $3,000 scheduled")).toBeInTheDocument();
      const spent = incomeBar().querySelector<HTMLElement>(":scope > div:not([data-expected])")!;
      expect(spent.style.background).toBe("var(--good)");
      const coming = incomeBar().querySelector<HTMLElement>("[data-expected]")!;
      expect(coming.style.background).toBe("var(--good)");
      expect(coming).toHaveClass("opacity-45");
      expect(incomeBar()).toHaveAttribute("aria-label", "50% of expected income received, 50% more scheduled");
      expect(screen.getByTitle("Where you'd be at an even pace today")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Roll over/ })).not.toBeInTheDocument();
    });

    it("says what's to come without anything scheduled, and nothing received yet", () => {
      setup(pay({ spent: 0, left: 6000 }), { income: true });
      expect(screen.getByText("$6,000 to come")).toBeInTheDocument();
    });

    it("is good news, not a warning, when more comes in than expected", () => {
      setup(pay({ spent: 6500, left: -500, expected: 100 }), { income: true });
      const over = screen.getByText("▲ $500 over");
      expect(over).toHaveClass("text-good");
      expect(over).not.toHaveClass("text-destructive");
      expect(incomeBar().querySelector(".bg-destructive")).toBeNull();
    });

    it("never turns red as a subcategory, even when what's scheduled would take it past", () => {
      setup(pay({ name: "Bonus", parent: "Paycheck", path: ["Paycheck", "Bonus"], depth: 1, budget: 100, spent: 90, left: 10, expected: 50 }),
        { income: true, sub: true, budgets: true });
      expect(screen.getByRole("link", { name: "$90" })).not.toHaveClass("text-destructive");
    });

    it("asks for what's expected rather than a budget", () => {
      setup(pay({ budget: null, left: null }), { income: true, budgets: true });
      expect(field("Paycheck")).toHaveTextContent("Expected");
    });
  });

  describe("in the Budgets card", () => {
    const actions = (name = "Groceries") => screen.getByRole("button", { name: `Actions for ${name}` });

    it("offers rollover in the row's ⋯ menu, with the row itself quiet until it's on", async () => {
      const { onchanged } = setup(cat(), { budgets: true });
      expect(screen.queryByText("Rolls over")).not.toBeInTheDocument();
      await userEvent.click(actions());
      await userEvent.click(screen.getByRole("button", { name: /Roll over/ }));
      expect(api).toHaveBeenCalledWith("/api/budget", { method: "POST", body: { category: "Groceries", rollover: true } });
      expect(toast.success).toHaveBeenCalledWith("Groceries rolls over from this month on");
      expect(onchanged).toHaveBeenCalled();
    });

    it("turns rollover off from the same menu", async () => {
      setup(cat({ rollover_from: "2026-01" }), { budgets: true });
      await userEvent.click(actions());
      const btn = screen.getByRole("button", { name: /Rolls over/ });
      expect(btn).toHaveAttribute("aria-pressed", "true");
      await userEvent.click(btn);
      expect(api).toHaveBeenCalledWith("/api/budget", { method: "POST", body: { category: "Groceries", rollover: false } });
    });

    it("says a rolling budget as a blue label that isn't a button", () => {
      setup(cat({ rollover_from: "2026-01" }), { budgets: true });
      const label = screen.getByText("Rolls over");
      expect(label).toHaveClass("text-primary");
      expect(label.closest("button")).toBeNull();
      expect(label).toHaveAttribute("title", expect.stringContaining("carries into the next"));
      expect(actions()).toHaveAttribute("aria-expanded", "false");
    });

    it("doesn't offer rollover on a subcategory, nor a card anywhere (that's in Settings › Categories)", () => {
      setup(cat(), { sub: true, budgets: true });
      expect(screen.queryByRole("button", { name: "Actions for Groceries" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Roll over/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });

    it("has no ⋯ menu until the category has a budget", () => {
      setup(cat({ budget: null }), { budgets: true });
      expect(screen.queryByRole("button", { name: "Actions for Groceries" })).not.toBeInTheDocument();
    });

    it("has no ⋯ menu outside the Budgets card", () => {
      setup(cat());
      expect(screen.queryByRole("button", { name: "Actions for Groceries" })).not.toBeInTheDocument();
    });

    it("closes the menu with Done", async () => {
      setup(cat(), { budgets: true });
      await userEvent.click(actions());
      expect(actions()).toHaveAttribute("aria-expanded", "true");
      await userEvent.click(screen.getByRole("button", { name: "Done" }));
      expect(screen.queryByRole("button", { name: /Roll over/ })).not.toBeInTheDocument();
    });

    it("keeps an income category free of the menu", () => {
      setup(cat({ name: "Paycheck", path: ["Paycheck"], top: "Paycheck", budget: 6000, spent: 3000, left: 3000 }), { income: true, budgets: true });
      expect(screen.queryByRole("button", { name: "Actions for Paycheck" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Roll over/ })).not.toBeInTheDocument();
    });
  });

  describe("the budget editor", () => {
    const own = (v: number) => cat({ budget: v, usual_budget: 500, month_budget: v, left: v - 200 });
    // jsdom can't lay the popover out, so Floating UI leaves it `visibility: hidden` and Testing Library would find no
    // names in it: shown here as a browser shows it once placed.
    const reveal = () => document.querySelectorAll<HTMLElement>("[data-bits-floating-content-wrapper]").forEach((el) => (el.style.visibility = "visible"));
    const amount = () => screen.getByLabelText("Amount") as HTMLInputElement;
    const radio = (name: string) => { reveal(); return screen.getByRole("radio", { name }); };
    const button = (name: string | RegExp) => { reveal(); return screen.getByRole("button", { name }); };
    const alert = () => { reveal(); return screen.getByRole("alert"); };
    const editor = () => document.querySelector("[data-budget-editor]");
    const posts = () => vi.mocked(api).mock.calls.map((x) => (x[1] as { body: unknown }).body);
    const open = async (name = "Groceries") => { await userEvent.click(field(name)); reveal(); return amount(); };
    const retype = async (v: string) => { await userEvent.clear(amount()); if (v) await userEvent.type(amount(), v); };
    const undoAction = () => {
      const call = vi.mocked(toast).mock.calls.at(-1)!;
      return (call[1] as unknown as { action: { onClick: () => Promise<void> } }).action;
    };
    // A request the test settles by hand.
    const deferred = () => {
      let settle!: { resolve: (v: unknown) => void; reject: (e: Error) => void };
      vi.mocked(api).mockImplementationOnce(() => new Promise((resolve, reject) => { settle = { resolve, reject }; }) as never);
      return () => settle;
    };
    afterEach(() => { viewport.phone = false; });

    it("opens titled for its category, with the amount focused, sending nothing", async () => {
      setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      expect(screen.getByRole("dialog", { name: "Edit Groceries budget" })).toBeInTheDocument();
      expect(amount()).toHaveFocus();
      expect(amount().value).toBe("500");
      expect(api).not.toHaveBeenCalled();
    });

    it("starts on the usual amount when the month has none of its own, and says what that means", async () => {
      setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      expect(radio("Usual monthly amount")).toHaveAttribute("aria-checked", "true");
      expect(radio("This month only")).toHaveAttribute("aria-checked", "false");
      expect(screen.getByText("Used in months without their own amount.")).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "Applies to" })).toBeInTheDocument();
    });

    it("starts on this month when it has its own amount, and names the month", async () => {
      setup(own(800), { budgets: true });
      expect(field()).toHaveAccessibleName("Edit Groceries budget: $800, Mar 2026 only");
      await open();
      expect(radio("This month only")).toHaveAttribute("aria-checked", "true");
      expect(amount().value).toBe("800");
      expect(screen.getByText("Applies to March 2026 only. Other months keep $500.")).toBeInTheDocument();
    });

    it("shows each scope's amount when switching, until one is typed, which it then keeps", async () => {
      setup(own(800), { budgets: true });
      await open();
      await userEvent.click(radio("Usual monthly amount"));
      expect(amount().value).toBe("500");
      expect(screen.getByText("Used in months without their own amount. March 2026 keeps its own $800.")).toBeInTheDocument();
      await userEvent.click(radio("This month only"));
      expect(amount().value).toBe("800");
      await retype("650");
      await userEvent.click(radio("Usual monthly amount"));
      expect(amount().value).toBe("650");
      expect(api).not.toHaveBeenCalled();
    });

    it("saves the usual amount with the existing payload, and says which amount it was", async () => {
      const { onchanged } = setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      await retype("650");
      await userEvent.click(button("Save"));
      expect(posts()).toEqual([{ category: "Groceries", amount: "650" }]);
      expect(toast.success).toHaveBeenCalledWith("Groceries: usual amount $650 a month");
      expect(onchanged).toHaveBeenCalledTimes(1);
      expect(editor()).toBeNull();
    });

    it("saves this month's amount for the month it was opened on, from Enter too", async () => {
      const { onchanged } = setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      await userEvent.click(radio("This month only"));
      expect(screen.getByText("Applies to March 2026 only. Other months keep $500.")).toBeInTheDocument();
      await retype("800");
      await userEvent.keyboard("{Enter}");
      expect(posts()).toEqual([{ category: "Groceries", month: "2026-03", amount: "800" }]);
      expect(toast.success).toHaveBeenCalledWith("Groceries: $800 in Mar 2026 only");
      expect(onchanged).toHaveBeenCalledTimes(1);
    });

    it("says a parent was raised, and for which months", async () => {
      vi.mocked(api).mockResolvedValueOnce({ ok: true, raised: [{ category: "Food", amount: 900 }] });
      setup(own(800), { budgets: true });
      await open();
      await userEvent.click(radio("Usual monthly amount"));
      await retype("650");
      await userEvent.click(button("Save"));
      expect(toast.success).toHaveBeenLastCalledWith("Groceries: usual amount $650 a month · Food usual amount raised to $900 · Mar 2026 keeps its own $800");
      vi.mocked(api).mockResolvedValueOnce({ ok: true, raised: [{ category: "Food", amount: 950 }] });
      await open();
      await retype("850");
      await userEvent.click(button("Save"));
      expect(posts()[1]).toEqual({ category: "Groceries", month: "2026-03", amount: "850" });
      expect(toast.success).toHaveBeenLastCalledWith("Groceries: $850 in Mar 2026 only · Food raised to $950 in Mar 2026 only");
    });

    it.each([["Cancel", () => userEvent.click(button("Cancel"))], ["Escape", () => userEvent.keyboard("{Escape}")]])(
      "%s sends nothing", async (_, close) => {
        const { onchanged } = setup(own(800), { budgets: true });
        await open();
        await userEvent.click(radio("Usual monthly amount"));
        await retype("1");
        await close();
        expect(editor()).toBeNull();
        expect(api).not.toHaveBeenCalled();
        expect(onchanged).not.toHaveBeenCalled();
        // Opened again, it starts from what's saved, not what was typed.
        await open();
        expect(amount().value).toBe("800");
        expect(radio("This month only")).toHaveAttribute("aria-checked", "true");
      });

    it("keeps 0 as this month's own amount, not the same as an empty box", async () => {
      setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      await userEvent.click(radio("This month only"));
      await retype("0");
      expect(screen.getByText("$0 budgets nothing in March 2026; other months keep $500.")).toBeInTheDocument();
      await userEvent.click(button("Save"));
      expect(posts()).toEqual([{ category: "Groceries", month: "2026-03", amount: "0" }]);
      expect(toast.success).toHaveBeenCalledWith("Groceries: $0 in Mar 2026 only");
      expect(toast).not.toHaveBeenCalled();   // not a reset: no Undo toast
    });

    it("takes the month back to the usual amount from an empty box, and Undo puts back exactly 0", async () => {
      const { onchanged } = setup(own(0), { budgets: true });
      await open();
      expect(amount().value).toBe("0");
      await retype("");
      expect(screen.getByText("No amount takes March 2026 back to the usual $500.")).toBeInTheDocument();
      await userEvent.click(button("Save"));
      expect(toast).toHaveBeenCalledWith("Groceries is back to $500 in Mar 2026", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
      expect(editor()).toBeNull();
      await undoAction().onClick();
      expect(posts()).toEqual([
        { category: "Groceries", month: "2026-03", amount: "" },
        { category: "Groceries", month: "2026-03", amount: "0" },
      ]);
      expect(onchanged).toHaveBeenCalledTimes(2);
    });

    it.each([["", ""], ["0", "0"]])("says an amount of %j as the usual one removes the budget, and sends it as before", async (typed, sent) => {
      setup(own(800), { budgets: true });
      await open();
      await userEvent.click(radio("Usual monthly amount"));
      await retype(typed);
      expect(screen.getByText("No amount removes the Groceries budget from every month, months with their own amount included.")).toBeInTheDocument();
      await userEvent.click(button("Save"));
      expect(posts()).toEqual([{ category: "Groceries", amount: sent }]);
      expect(toast.success).toHaveBeenCalledWith("Groceries budget removed");
    });

    it("won't save an empty box where there's nothing to clear", async () => {
      setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      await userEvent.click(radio("This month only"));
      await retype("");
      expect(screen.getByText("Enter an amount for March 2026.")).toBeInTheDocument();
      expect(button("Save")).toBeDisabled();
      await userEvent.keyboard("{Enter}");
      expect(api).not.toHaveBeenCalled();
    });

    it("sets a first budget as the usual amount, without offering a month of its own", async () => {
      setup(cat({ budget: null, left: null }), { budgets: true });
      await open();
      expect(amount().value).toBe("");
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.getByText(/usual monthly amount/)).toBeInTheDocument();
      expect(button("Save")).toBeDisabled();
      await retype("300");
      await userEvent.click(button("Save"));
      expect(posts()).toEqual([{ category: "Groceries", amount: "300" }]);
    });

    it("offers no month of its own outside the Budgets card", async () => {
      setup(cat());
      await open();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    });

    it("keeps the editor open with the error when saving fails, and saves on a retry", async () => {
      vi.mocked(api).mockRejectedValueOnce(new Error("Couldn’t reach the server"));
      const { onchanged } = setup(cat({ usual_budget: 500 }), { budgets: true });
      await open();
      await retype("650");
      await userEvent.click(button("Save"));
      expect(alert()).toHaveTextContent("Couldn’t reach the server");
      expect(editor()).not.toBeNull();
      expect(amount().value).toBe("650");
      expect(radio("Usual monthly amount")).toHaveAttribute("aria-checked", "true");
      expect(toast.success).not.toHaveBeenCalled();
      expect(toast.error).not.toHaveBeenCalled();
      expect(onchanged).not.toHaveBeenCalled();
      await userEvent.click(button("Save"));
      expect(posts()).toEqual([{ category: "Groceries", amount: "650" }, { category: "Groceries", amount: "650" }]);
      expect(editor()).toBeNull();
      expect(onchanged).toHaveBeenCalledTimes(1);
    });

    it("resets the month to usual from the editor, and Undo puts back exactly what it was", async () => {
      setup(own(1234567.89), { budgets: true });
      await open();
      await userEvent.click(button(/^Reset this month to usual/));
      expect(editor()).toBeNull();
      await undoAction().onClick();
      expect(posts()).toEqual([
        { category: "Groceries", month: "2026-03", amount: "" },
        { category: "Groceries", month: "2026-03", amount: "1234567.89" },
      ]);
    });

    it("keeps the editor open with the error when a reset from it fails", async () => {
      vi.mocked(api).mockRejectedValueOnce(new Error("Couldn’t reach the server"));
      vi.mocked(toast).mockClear();
      setup(own(800), { budgets: true });
      await open();
      await userEvent.click(button(/^Reset this month to usual/));
      expect(alert()).toHaveTextContent("Couldn’t reach the server");
      expect(editor()).not.toBeNull();
      expect(toast).not.toHaveBeenCalled();
    });

    it("can't be opened, or saved, while the page's figures may be out of date", async () => {
      const props = { c: cat({ usual_budget: 500 }), month: "2026-03", pace: 0.5, payAccounts: pay, budgets: true, onchanged: vi.fn() };
      const { rerender } = render(BudgetRow, { ...props, locked: true });
      expect(field()).toBeDisabled();
      await rerender({ ...props, locked: false });
      await open();
      await retype("650");
      await rerender({ ...props, locked: true });
      expect(button("Save")).toBeDisabled();
      expect(screen.getByText("Saving is off until the page’s figures are up to date.")).toBeInTheDocument();
      await userEvent.keyboard("{Enter}");
      expect(api).not.toHaveBeenCalled();
    });

    describe("when another month comes on screen", () => {
      const props = { c: cat({ usual_budget: 500 }), month: "2026-03", pace: 0.5, payAccounts: pay, budgets: true, onchanged: vi.fn() };

      it("closes the editor without sending anything", async () => {
        const { rerender } = render(BudgetRow, props);
        await open();
        await userEvent.click(radio("This month only"));
        await retype("800");
        await rerender({ ...props, month: "2026-04" });
        expect(editor()).toBeNull();
        expect(api).not.toHaveBeenCalled();
      });

      it("finishes a save already sent for the month it was opened on, and says that month", async () => {
        const settle = deferred();
        const { rerender } = render(BudgetRow, props);
        await open();
        await userEvent.click(radio("This month only"));
        await retype("800");
        await userEvent.click(button("Save"));
        expect(button("Saving…")).toBeDisabled();
        await rerender({ ...props, month: "2026-04" });
        settle().resolve({ ok: true, raised: [] });
        await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Groceries: $800 in Mar 2026 only"));
        expect(posts()).toEqual([{ category: "Groceries", month: "2026-03", amount: "800" }]);
        expect(editor()).toBeNull();
      });

      it("says a save that failed meanwhile in a toast, closing the editor", async () => {
        const settle = deferred();
        const { rerender } = render(BudgetRow, props);
        await open();
        await retype("650");
        await userEvent.click(button("Save"));
        await rerender({ ...props, month: "2026-04" });
        settle().reject(new Error("Couldn’t reach the server"));
        await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith("Couldn’t reach the server"));
        expect(editor()).toBeNull();
        expect(posts()).toEqual([{ category: "Groceries", amount: "650" }]);
      });
    });

    it("is a sheet from the bottom on a phone, with the amount focused", async () => {
      viewport.phone = true;
      setup(own(800), { budgets: true });
      await open();
      const sheet = screen.getByRole("dialog", { name: "Edit Groceries budget" });
      expect(sheet).toHaveAttribute("data-budget-sheet");
      expect(amount()).toHaveFocus();
      expect(amount()).toHaveClass("text-base", "h-11");
      expect(radio("This month only")).toHaveAttribute("aria-checked", "true");
      await userEvent.click(button("Cancel"));
      expect(editor()).toBeNull();
      expect(api).not.toHaveBeenCalled();
    });
  });

  describe("a month of its own", () => {
    it("says so under the row, with the usual amount the other months keep", () => {
      setup(cat({ budget: 800, usual_budget: 500, month_budget: 800, left: 600 }), { budgets: true });
      expect(screen.getByText("Mar 2026 only · $500 other months")).toBeInTheDocument();
      expect(field()).toHaveTextContent("$800");
      expect(field().querySelector(".text-primary")).not.toBeNull();
    });

    it("isn't noted without one", () => {
      setup(cat({ usual_budget: 500 }), { budgets: true });
      expect(screen.queryByText(/other months/)).not.toBeInTheDocument();
    });

    describe("Reset to usual", () => {
      const own = (v: number) => cat({ budget: v, usual_budget: 500, month_budget: v, left: v - 200 });
      const reset = () => screen.getByRole("button", { name: /^Reset to usual/ });
      const undoAction = () => {
        const call = vi.mocked(toast).mock.calls.at(-1)!;
        const { action } = call[1] as unknown as { action: { onClick: () => Promise<void> } };
        return action;
      };
      const posts = () => vi.mocked(api).mock.calls.map((x) => (x[1] as { body: unknown }).body);

      it("is a visible button beside the note, only while the month has its own amount", () => {
        const { unmount } = render(BudgetRow, { c: own(800), month: "2026-03", pace: 0.5, payAccounts: pay, budgets: true, onchanged: vi.fn() });
        expect(reset()).toBeVisible();
        expect(reset().className).not.toMatch(/hoverable|opacity-0|group-hover/);
        expect(reset().closest("[data-month-note]")).toHaveTextContent("Mar 2026 only · $500 other months");
        unmount();
        render(BudgetRow, { c: cat({ usual_budget: 500 }), month: "2026-03", pace: 0.5, payAccounts: pay, budgets: true, onchanged: vi.fn() });
        expect(screen.queryByRole("button", { name: /^Reset to usual/ })).not.toBeInTheDocument();
      });

      it("clears the month's amount from the keyboard and offers Undo", async () => {
        const { onchanged } = setup(own(800), { budgets: true });
        reset().focus();
        await userEvent.keyboard("{Enter}");
        expect(posts()).toEqual([{ category: "Groceries", month: "2026-03", amount: "" }]);
        expect(toast).toHaveBeenCalledWith("Groceries is back to $500 in Mar 2026", expect.anything());
        expect(onchanged).toHaveBeenCalledTimes(1);
      });

      it.each([0, 0.1, 1234567.89, 99999999])("Undo puts back exactly %s", async (v) => {
        const { onchanged } = setup(own(v), { budgets: true });
        await userEvent.click(reset());
        await undoAction().onClick();
        expect(posts()).toEqual([
          { category: "Groceries", month: "2026-03", amount: "" },
          { category: "Groceries", month: "2026-03", amount: String(v) },
        ]);
        expect(Number((posts()[1] as { amount: string }).amount)).toBe(v);
        expect(onchanged).toHaveBeenCalledTimes(2);
      });

      it("Undo restores the month it was taken from, after another is on screen", async () => {
        const props = { c: own(0.1), month: "2026-03", pace: 0.5, payAccounts: pay, budgets: true, onchanged: vi.fn() };
        const { rerender } = render(BudgetRow, props);
        await userEvent.click(reset());
        await rerender({ ...props, c: cat({ usual_budget: 500 }), month: "2026-04" });
        await undoAction().onClick();
        expect(posts()[1]).toEqual({ category: "Groceries", month: "2026-03", amount: "0.1" });
      });

      it("shows the error and keeps the month's amount when the request fails", async () => {
        vi.mocked(api).mockRejectedValueOnce(new Error("Couldn’t reach the server"));
        vi.mocked(toast).mockClear();
        const { onchanged } = setup(own(800), { budgets: true });
        await userEvent.click(reset());
        expect(toast.error).toHaveBeenCalledWith("Couldn’t reach the server");
        expect(toast).not.toHaveBeenCalled();   // no "back to the usual" message, no Undo for something that didn't happen
        expect(field()).toHaveTextContent("$800");
        expect(screen.getByText(/Mar 2026 only/)).toBeInTheDocument();
        expect(reset()).toBeEnabled();   // can be tried again
        expect(onchanged).toHaveBeenCalledTimes(1);   // reloads, so the row shows what's saved
      });

      it("shows the error when Undo fails", async () => {
        setup(own(800), { budgets: true });
        await userEvent.click(reset());
        vi.mocked(api).mockRejectedValueOnce(new Error("Set a budget for this category first"));
        await undoAction().onClick();
        expect(toast.error).toHaveBeenCalledWith("Set a budget for this category first");
      });
    });
  });
});
