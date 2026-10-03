// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/filters.svelte", async (orig) => ({ ...(await orig<typeof import("$lib/filters.svelte")>()), showTransactions: vi.fn() }));

import { categories } from "$lib/categories.svelte";
import { showTransactions } from "$lib/filters.svelte";
import type { Category } from "$lib/types";
import Sankey from "./Sankey.svelte";
import type { Cashflow } from "./types";

const cf: Cashflow = {
  month: "2026-03",
  income: [{ name: "Salary", value: 4000 }],
  spending: [
    { name: "Housing", value: 1500, children: [{ name: "Rent", value: 1200 }, { name: "Housing (general)", value: 300 }] },
    { name: "Groceries", value: 500, children: [] },
    { name: "Uncategorized", value: 200, children: [] },
    { name: "Pets", value: 20, children: [] }, { name: "Gifts", value: 10, children: [] },
  ],
  total_in: 4000, total_out: 2230, net: 1770,
};
let width = 800;
const real = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth")!;
beforeEach(() => {
  width = 800;
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => width });
  vi.mocked(showTransactions).mockReset();
  categories.list = [{ name: "Groceries", color: "#008300", icon: "🛒" }, { name: "Housing", color: "#3987e5", icon: "🏠" }] as Category[];
});
afterEach(() => { delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth; void real; categories.list = []; });

const mark = (name: RegExp) => screen.getByRole("button", { name });
const detail = () => document.querySelector("[data-selection]")!;

describe("Sankey", () => {
  it("draws nothing until it's been measured", () => {
    width = 0;
    render(Sankey, { cf, monthName: "March" });
    expect(document.querySelector("svg")).toBeNull();
  });

  it("colors spending by category, and everything else gray", () => {
    render(Sankey, { cf, monthName: "March" });
    expect(mark(/^Groceries: \$500/).style.fill).toBe("rgb(0, 131, 0)");
    expect(mark(/^Rent: \$1,200/).style.fill).toBe("rgb(57, 135, 229)");   // a subcategory wears its category's color
    expect(mark(/^Everything else \(2\): \$30/).style.fill).toBe("var(--cat-other)");
    expect(mark(/^Salary: \$4,000/).style.fill).toBe("var(--flow-in)");
  });

  it("names each mark with its amount and share", () => {
    render(Sankey, { cf, monthName: "March" });
    expect(mark(/^Groceries: \$500, 22% of money out$/)).toHaveAttribute("tabindex", "0");
    expect(mark(/^Salary → March: \$4,000, 100% of money in$/)).toBeInTheDocument();
  });

  it("opens what's behind a band or a bar with a click", async () => {
    render(Sankey, { cf, monthName: "March" });
    const click = async (el: Element) => { await fireEvent.pointerDown(el, { pointerType: "mouse" }); await fireEvent.click(el); };
    await click(mark(/^Groceries: /));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "Groceries" });
    await click(mark(/^Housing → Rent: /));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "Rent" });
    await click(mark(/^Housing \(general\): /));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "Housing" });
    await click(mark(/^Uncategorized: /));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "__none__" });
    await click(mark(/^Everything else \(2\): /));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", kind: "out" });
    await click(mark(/^Salary: /));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "Salary" });
  });

  it("on a touch screen, the first tap selects (its line stays under the chart) and the second opens", async () => {
    render(Sankey, { cf, monthName: "March" });
    // as a browser does it: the tap focuses the mark between its pointerdown and its click
    const tap = async (el: Element) => { await fireEvent.pointerDown(el, { pointerType: "touch" }); await fireEvent.focus(el); await fireEvent.click(el); };
    await tap(mark(/^Groceries: /));
    expect(showTransactions).not.toHaveBeenCalled();
    expect(detail()).toHaveTextContent("Groceries · $500 · 22% of money out");
    await tap(mark(/^Groceries: /));
    expect(showTransactions).toHaveBeenCalledWith({ scope: "budget", month: "2026-03", category: "Groceries" });
  });

  it("selects on keyboard focus and opens with Enter", async () => {
    render(Sankey, { cf, monthName: "March" });
    await fireEvent.focus(mark(/^Housing: /));
    expect(detail()).toHaveTextContent("Housing · $1,500 · 67% of money out");
    await fireEvent.click(screen.getByRole("button", { name: "Transactions" }));
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "Housing" });
    await fireEvent.keyDown(mark(/^Groceries: /), { key: "Enter" });
    expect(showTransactions).toHaveBeenLastCalledWith({ scope: "budget", month: "2026-03", category: "Groceries" });
  });

  it("selects what's left over, which has no transactions of its own", async () => {
    render(Sankey, { cf, monthName: "March" });
    await fireEvent.pointerDown(mark(/^Left over: /), { pointerType: "mouse" });
    await fireEvent.click(mark(/^Left over: /));
    expect(showTransactions).not.toHaveBeenCalled();
    expect(detail()).toHaveTextContent("Left over · $1,770");
    expect(screen.queryByRole("button", { name: "Transactions" })).toBeNull();
  });

  it("narrow, it labels money in too, with amounts, and drops the subcategories", () => {
    width = 360;
    render(Sankey, { cf, monthName: "March" });
    const texts = [...document.querySelectorAll("svg text")].map((t) => t.textContent);
    expect(texts).toContain("Salary$4,000");
    expect(texts).toContain("Groceries$500");
    expect(screen.queryByRole("button", { name: /^Rent: / })).toBeNull();
  });

  it("from 560px up it's laid out at least 620 wide and scaled to fit, not scrolled", () => {
    width = 580;
    render(Sankey, { cf, monthName: "March" });
    const svg = document.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toMatch(/^0 0 620 /);
    expect(svg.getAttribute("width")).toBe("100%");
    expect(document.querySelector(".min-w-\\[720px\\]")).toBeNull();
  });
});
