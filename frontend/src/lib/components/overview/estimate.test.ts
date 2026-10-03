import { describe, expect, it } from "vitest";
import type { StatementEstimate } from "$lib/types";
import { estimateLines, estimateText, estimateTitle } from "./estimate";

const base = { close: "2026-11-01", due: "2026-11-26" } as const;
const text = (est: StatementEstimate) => estimateLines(est).map(estimateText);

describe("estimate breakdown", () => {
  it("lists the parts of an estimate from budgets, the sum last", () => {
    const est: StatementEstimate = {
      ...base, basis: "budgets", charged_so_far: 1204.1, usual: 604.45, outside_average: 1066.67, days_left: 17, days_left_share: 0.5667,
      budgets: [{ category: "Groceries", amount: 600 }, { category: "Restaurants", amount: 250 }, { category: "Gas", amount: 100 },
        { category: "Pets", amount: 20 }, { category: "Gifts", amount: 10 }], budgets_total: 980,
      cycles: [{ close: "2026-08-01", amount: 1200 }, { close: "2026-09-01", amount: 800 }, { close: "2026-10-01", amount: 1200 }],
      statement: 2788.55, total: 2788.55,
    };
    // (dates keep their no-break space)
    expect(estimateTitle(est).replace(/\u00a0/g, " ").split("\n")).toEqual([
      "Estimate for the Nov 1 statement",
      "Charged so far · $1,204.10",
      "Budgets on this card to Nov 1 · $980.00 (Groceries $600.00, Restaurants $250.00, Gas $100.00, …)",
      "Usual other spending, 17 days left · $604.45 (57% of $1,066.67, the average of $1,200.00, $800.00, $1,200.00)",
      "= $2,788.55",
    ]);
  });

  it("a later statement: the whole average, a recurring charge and an annual fee on top", () => {
    expect(text({
      ...base, basis: "average", usual: 1066.67, average: 1066.67,
      cycles: [{ close: "2026-08-01", amount: 1200 }, { close: "2026-09-01", amount: 800 }, { close: "2026-10-01", amount: 1200 }],
      separate: [{ name: "Insurance", amount: 600 }], separate_total: 600,
      fees: [{ name: "Sapphire annual fee", amount: 95 }], fees_total: 95, statement: 1761.67, total: 1761.67,
    })).toEqual([
      "Usual spending · $1,066.67 (average of $1,200.00, $800.00, $1,200.00)",
      "Insurance · $600.00",
      "Sapphire annual fee · $95.00",
      "= $1,761.67",
    ]);
  });

  it("lists four budgets in full rather than hiding one behind “…”", () => {
    expect(text({ ...base, basis: "budgets", usual: 0, outside_average: 0, cycles: [], budgets_total: 40, statement: 40, total: 40,
      budgets: ["A", "B", "C", "D"].map((category) => ({ category, amount: 10 })) })[0]).toBe(
      "Budgets on this card to Nov\u00a01 · $40.00 (A $10.00, B $10.00, C $10.00, D $10.00)");
  });

  it("names several recurring charges together, and one averaged statement as such", () => {
    expect(text({
      ...base, basis: "average", usual: 500, average: 500, cycles: [{ close: "2026-10-01", amount: 500 }],
      separate: [{ name: "Insurance", amount: 600 }, { name: "Streaming", amount: 15 }], separate_total: 615, statement: 1115, total: 1115,
    }).slice(0, 2)).toEqual(["Usual spending · $500.00 (as on the last statement)", "Recurring charges · $615.00 (Insurance $600.00, Streaming $15.00)"]);
  });

  it("the recent daily rate, without enough history for an average", () => {
    expect(text({ ...base, basis: "recent", charged_so_far: 300, usual: 170, daily_rate: 10, days: 17, statement: 470, total: 470 }))
      .toEqual(["Charged so far · $300.00", "Recent spending · $170.00 ($10.00 a day for 17 days)", "= $470.00"]);
  });

  it("a carried balance, its interest, and paying the minimum", () => {
    const lines = text({
      ...base, basis: "average", usual: 1000, average: 1000, cycles: [{ close: "2026-09-01", amount: 900 }, { close: "2026-10-01", amount: 1100 }],
      carried: 550, interest: 20.5, apr: 24, statement: 1570.5, total: 36.21, pay_mode: "minimum",
    });
    expect(lines.slice(1)).toEqual(["Carried from the last statement · $550.00", "Interest · $20.50 (24% APR)", "= $1,570.50",
      "Paying the minimum · $36.21 (the rest carries over)"]);
  });

  it("a credit on the card, with a real minus", () => {
    const lines = text({ ...base, basis: "average", usual: 100, average: 100, cycles: [], carried: -50, statement: 50, total: 50 });
    expect(lines).toContain("Credit on the card · −$50.00");
  });
});
