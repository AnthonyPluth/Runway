import { describe, expect, it } from "vitest";
import type { StatementEstimate } from "$lib/types";
import { estimateLines, estimateText, estimateTitle } from "./estimate";

const base = { close: "2026-11-01", due: "2026-11-26" } as const;
// (dates keep their no-break space)
const text = (est: StatementEstimate) => estimateLines(est).map((l) => estimateText(l).replace(/\u00a0/g, " "));

describe("estimate breakdown", () => {
  it("lists what's charged and the budgets on the card, the sum last", () => {
    const est: StatementEstimate = {
      ...base, charged_so_far: 1204.1,
      budgets: [{ category: "Groceries", amount: 600 }, { category: "Restaurants", amount: 250 }, { category: "Gas", amount: 100 },
        { category: "Pets", amount: 20 }, { category: "Gifts", amount: 10 }], budgets_total: 980,
      recurring: [{ name: "Insurance", amount: 600 }, { name: "Streaming", amount: 4.45 }], recurring_total: 604.45,
      statement: 2788.55, total: 2788.55,
    };
    expect(estimateTitle(est).replace(/\u00a0/g, " ").split("\n")).toEqual([
      "Estimate for the Nov 1 statement",
      "Charged so far · $1,204.10",
      "Budgets on this card to Nov 1 · $980.00 (Groceries $600.00, Restaurants $250.00, Gas $100.00, …)",
      "Recurring charges · $604.45 (Insurance $600.00, Streaming $4.45)",
      "= $2,788.55",
    ]);
  });

  it("lists four budgets in full rather than hiding one behind “…”, and names a single one", () => {
    expect(text({ ...base, budgets_total: 40, statement: 40, total: 40, budgets: ["A", "B", "C", "D"].map((category) => ({ category, amount: 10 })) })[0])
      .toBe("Budgets on this card to Nov 1 · $40.00 (A $10.00, B $10.00, C $10.00, D $10.00)");
    expect(text({ ...base, budgets_total: 40, statement: 40, total: 40, budgets: [{ category: "Groceries", amount: 40 }] })[0])
      .toBe("Budgets on this card to Nov 1 · $40.00 (Groceries)");
  });

  it("a later statement: one recurring charge and an annual fee, each its own line", () => {
    expect(text({
      ...base, recurring: [{ name: "Insurance", amount: 600 }], recurring_total: 600,
      fees: [{ name: "Sapphire annual fee", amount: 95 }], fees_total: 95, statement: 695, total: 695,
    })).toEqual(["Insurance · $600.00", "Sapphire annual fee · $95.00", "= $695.00"]);
  });

  it("a carried balance, its interest, and paying the minimum", () => {
    expect(text({ ...base, charged_so_far: 1000, carried: 550, interest: 20.5, apr: 24, statement: 1570.5, total: 36.21, pay_mode: "minimum" }))
      .toEqual(["Charged so far · $1,000.00", "Carried from the last statement · $550.00", "Interest · $20.50 (24% APR)", "= $1,570.50",
        "Paying the minimum · $36.21 (the rest carries over)"]);
    expect(text({ ...base, charged_so_far: 100, statement: 100, total: 50, pay_mode: "fixed" }).at(-1)).toBe("Paying your fixed amount · $50.00 (the rest carries over)");
  });

  it("a credit, with a real minus", () => {
    expect(text({ ...base, charged_so_far: 100, carried: -50, statement: 50, total: 50 })).toContain("Credit from the last statement · −$50.00");
  });

  it("a card with no statement yet: what it owes now, on a cycle taken to end with the month", () => {
    const est: StatementEstimate = { close: "2026-10-31", due: "2026-11-25", assumed_cycle: true, owed_now: 40,
      budgets: [{ category: "Travel", amount: 310 }], budgets_total: 310, statement: 350, total: 350 };
    expect(estimateTitle(est).replace(/\u00a0/g, " ").split("\n")).toEqual(["Estimate for a statement closing Oct 31 (none from the bank yet)",
      "Owed on the card now · $40.00", "Budgets on this card to Oct 31 · $310.00 (Travel)", "= $350.00"]);
    expect(text({ ...est, owed_now: -20, statement: 290, total: 290 })[0]).toBe("Credit on the card now · −$20.00");
  });
});
