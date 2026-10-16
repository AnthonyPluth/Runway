import { describe, expect, it } from "vitest";
import { tx } from "../../../test/fixtures";
import { alternatives } from "./reviewCard";
import { dayLabel, groupDays } from "./txDays";

const today = new Date(2026, 2, 10);   // 10 March 2026

describe("dayLabel", () => {
  it("says Today and Yesterday, then the date", () => {
    expect(dayLabel("2026-03-10", today)).toBe("Today");
    expect(dayLabel("2026-03-09", today)).toBe("Yesterday");
    const plain = (s: string) => s.replace(/\s/g, " ");   // dates never break across lines: their spaces are non-breaking
    expect(plain(dayLabel("2026-03-07", today))).toBe("Saturday, Mar 7");
    expect(plain(dayLabel("2025-12-31", today))).toBe("Wed, Dec 31, 2025");
  });

  it("Yesterday is right across a month's start", () => {
    expect(dayLabel("2026-02-28", new Date(2026, 2, 1))).toBe("Yesterday");
  });
});

describe("groupDays", () => {
  const counted = (t: { amount: number; category: string | null }) => (t.category === "Card payment" ? 0 : t.amount);
  const left = (t: { category: string | null }) => t.category === "Card payment";

  it("groups by day in order, each with its net total, transfers not counted", () => {
    const days = groupDays([
      tx({ id: "1", posted: "2026-03-10", amount: -20 }),
      tx({ id: "2", posted: "2026-03-10", amount: 100.5 }),
      tx({ id: "3", posted: "2026-03-10", amount: -300, category: "Card payment" }),
      tx({ id: "4", posted: "2026-03-09", amount: -4.25 }),
    ], counted, left);
    expect(days.map((d) => [d.day, d.net, d.transfers, d.rows.map((r) => r.t.id)])).toEqual([
      ["2026-03-10", 80.5, true, ["1", "2", "3"]],
      ["2026-03-09", -4.25, false, ["4"]],
    ]);
    expect(days[0].rows.map((r) => r.i)).toEqual([0, 1, 2]);
  });
});

describe("alternatives", () => {
  const cats = ["Coffee", "Groceries", "Dining", "Gas", "Card payment"].map((name) => ({ name, path: [name], depth: 0, top: name, is_transfer: name === "Card payment" ? 1 : 0 }));
  it("offers the two most used other categories of the same direction, never a transfer's", () => {
    const t = tx({ id: "t", category: "Coffee", amount: -5 });
    const rows = [
      ...["Groceries", "Groceries", "Dining", "Dining", "Dining", "Gas", "Coffee", "Card payment"].map((category, i) => tx({ id: `r${i}`, category, amount: -10 })),
      tx({ id: "in", category: "Gas", amount: 50 }),
    ];
    expect(alternatives(t, rows, cats)).toEqual(["Dining", "Groceries"]);
  });
});

describe("dayLabel with the host's day", () => {
  it("takes it as a YYYY-MM-DD, whatever the browser's day is", () => {
    expect(dayLabel("2026-03-10", "2026-03-10")).toBe("Today");
    expect(dayLabel("2026-03-09", "2026-03-10")).toBe("Yesterday");
    expect(dayLabel("2026-03-10", "2026-03-11")).toBe("Yesterday");
  });
});
