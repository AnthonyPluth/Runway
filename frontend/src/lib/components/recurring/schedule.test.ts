import { describe, expect, it } from "vitest";
import { byDue, dueIn, dueLabel, lateBy, monthlyTotal, perMonth } from "./schedule";
import type { RecurringItem } from "./types";

const nb = (s: string) => s.replace(/ /g, "\u00a0");
const item = (extra: Partial<RecurringItem> = {}): RecurringItem => ({
  id: 1, name: "Rent", account_id: "a1", amount: -1500, frequency: "monthly", anchor_date: "2026-03-01", active: 1, matched_count: 0, ...extra,
});

describe("when an item comes", () => {
  it("says how far off it is up to a month, then the date", () => {
    expect(dueIn("2026-10-03", "2026-10-03")).toBe("today");
    expect(dueIn("2026-10-04", "2026-10-03")).toBe("tomorrow");
    expect(dueIn("2026-10-12", "2026-10-03")).toBe("in 9 days");
    expect(dueIn("2026-11-02", "2026-10-03")).toBe("in 30 days");
    expect(dueIn("2026-11-20", "2026-10-03")).toBe(`next ${nb("Nov 20")}`);
    expect(dueIn("2026-11-02", "2026-10-31")).toBe("in 2 days");
  });

  it("says how late, in days", () => {
    expect(lateBy("2026-09-30", "2026-10-03")).toBe("3 days late");
    expect(lateBy("2026-10-02", "2026-10-03")).toBe("1 day late");
  });

  it("puts a late one first, as late; says nothing for a paused one; a one-time one says its date", () => {
    expect(dueLabel(item({ late_date: "2026-09-30", next_date: "2026-11-01" }), "2026-10-03")).toEqual({ text: "3 days late", late: true });
    expect(dueLabel(item({ next_date: "2026-10-12" }), "2026-10-03")).toEqual({ text: "in 9 days", late: false });
    expect(dueLabel(item({ active: 0, next_date: "2026-10-12" }), "2026-10-03")).toBeNull();
    expect(dueLabel(item({ frequency: "once", next_date: "2026-12-01" }), "2026-10-03")?.text).toBe(nb("Dec 1"));
    expect(dueLabel(item({ frequency: "once", anchor_date: "2026-05-01", next_date: null }), "2026-10-03")?.text).toBe(nb("May 1"));
    expect(dueLabel(item({ next_date: null }), "2026-10-03")?.text).toBe("no upcoming date");
  });

  it("sorts late ones first, then soonest due, then ones with no date, then paused ones", () => {
    const list = [item({ id: 1, name: "Paused", active: 0, next_date: "2026-10-04" }), item({ id: 2, name: "None", next_date: null }),
      item({ id: 3, name: "Later", next_date: "2026-10-20" }), item({ id: 4, name: "Late", late_date: "2026-10-01", next_date: "2026-11-01" }),
      item({ id: 5, name: "Soon", next_date: "2026-10-05" })];
    expect(list.sort(byDue).map((r) => r.name)).toEqual(["Late", "Soon", "Later", "None", "Paused"]);
  });
});

describe("what items come to in a month", () => {
  it("normalizes each schedule to a month", () => {
    expect(perMonth("monthly")).toBe(1);
    expect(perMonth("weekly")).toBeCloseTo(52 / 12);
    expect(perMonth("biweekly")).toBeCloseTo(26 / 12);
    expect(perMonth("semimonthly", "1,15")).toBe(2);
    expect(perMonth("semimonthly", "")).toBe(2);
    expect(perMonth("quarterly")).toBeCloseTo(1 / 3);
    expect(perMonth("semiannual")).toBeCloseTo(1 / 6);
    expect(perMonth("yearly")).toBeCloseTo(1 / 12);
    expect(perMonth("dates", "04-15,10-15")).toBeCloseTo(2 / 12);
    expect(perMonth("once")).toBe(0);
  });

  it("adds them up at the amount the forecast expects, leaving out paused ones", () => {
    const list = [item({ amount: -1200 }), item({ amount: -50, expected_amount: -60, frequency: "weekly" }),
      item({ amount: -600, frequency: "yearly" }), item({ amount: -999, active: 0 }), item({ amount: -400, frequency: "once" })];
    expect(monthlyTotal(list)).toBeCloseTo(1200 + 60 * 52 / 12 + 50);
    expect(monthlyTotal([item({ amount: 1500, frequency: "biweekly" })])).toBeCloseTo(3250);
  });
});
