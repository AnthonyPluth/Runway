import { describe, expect, it } from "vitest";
import {
  bankLeft, bankOrder, bonusLabel, cardOrder, daysUntil, eligibilityText, feesDue, five24Line, mine, points, spendProgress,
} from "./churning";
import type { BankBonus, ChurnCard, Eligibility, Five24 } from "./types";

const TODAY = "2026-09-29";
// Dates come with non-breaking spaces (so they never wrap); compare them as plain text.
const sp = (s: string) => s.replace(/\u00a0/g, " ");
const e = (x: Partial<Eligibility>): Eligibility => ({ status: "now", on: null, why: "", override: false, ...x });

describe("churning helpers", () => {
  it("filters by person", () => {
    const items = [{ owner: "Alex" }, { owner: "Sam" }, { owner: null }];
    expect(mine(items, "")).toHaveLength(3);
    expect(mine(items, "Sam")).toEqual([{ owner: "Sam" }]);
  });

  it("counts days and shortens points", () => {
    expect(daysUntil("2026-10-01", TODAY)).toBe(2);
    expect(daysUntil("2027-03-29", TODAY)).toBe(181);   // across a DST change, still whole days
    expect(points(60000)).toBe("60k");
    expect(points(1250)).toBe("1,250");
    expect(points(null)).toBe("—");
    expect(bonusLabel(200, "cash", "Cash back")).toBe("$200");
    expect(bonusLabel(75000, "ur", "Chase Ultimate Rewards")).toBe("75k Ultimate Rewards");
    expect(bonusLabel(null, "ur", "x")).toBe("");
  });

  it("works out spending progress", () => {
    expect(spendProgress(1500, 6000)).toEqual({ share: 0.25, left: 4500 });
    expect(spendProgress(7000, 6000)).toEqual({ share: 1, left: 0 });
    expect(spendProgress(null, null)).toEqual({ share: 0, left: 0 });
  });

  it("words eligibility", () => {
    expect(sp(eligibilityText(e({}), TODAY))).toBe("Now");
    expect(sp(eligibilityText(e({ status: "later", on: "2027-09-15" }), TODAY))).toBe("Sep 15, 2027");
    expect(sp(eligibilityText(e({ status: "later", on: "2027-09-15", override: true }), TODAY))).toBe("Sep 15, 2027 (your date)");
    expect(sp(eligibilityText(e({ status: "never" }), TODAY))).toBe("Never (once per lifetime)");
    expect(sp(eligibilityText(e({ status: "held", on: TODAY }), TODAY))).toBe("After you close or downgrade it");
    expect(sp(eligibilityText(e({ status: "held", on: "2027-01-01" }), TODAY))).toBe("After you close it, from Jan 1, 2027");
    expect(sp(eligibilityText(e({ status: "unknown" }), TODAY))).toBe("Rule unknown");
  });

  it("sums up 5/24", () => {
    const f = { count: 6, under: false, under_on: "2027-01-10", next_fall_off: "2026-10-15" } as Five24;
    expect(sp(five24Line(f).next)).toBe("Under 5/24 on Jan 10, 2027");
    expect(sp(five24Line({ ...f, count: 3, under: true, under_on: null }).next)).toBe("2/24 on Oct 15, 2026");
    expect(five24Line(undefined).count).toBe("0/24");
  });

  it("sorts, totals fees and lists what's left of a bank bonus", () => {
    const c = (id: number, status: string, opened: string, fee_due: string | null = null, annual_fee = 0) =>
      ({ id, status, opened_on: opened, fee_due, annual_fee }) as ChurnCard;
    const cards = [c(1, "closed", "2026-01-01"), c(2, "open", "2024-01-01", "2026-10-10", 95), c(3, "open", "2025-01-01", "2027-06-01", 550)];
    expect([...cards].sort(cardOrder).map((x) => x.id)).toEqual([3, 2, 1]);
    expect(feesDue(cards, TODAY)).toEqual({ total: 95, count: 1 });
    const b = { id: 1, state: "active", opened_on: "2026-08-01", dd_total: 1000, dd_count: 2, debit_count: 1, min_balance: 1500,
      progress: { dd_total: 400, dd_count: 1, debits: 1, balance: 1000, balance_ok: false } } as BankBonus;
    expect(bankLeft(b)).toEqual(["$600 more direct deposits", "1 more deposit", "$500 more to reach the minimum balance"]);
    const done = { ...b, id: 2, state: "received" } as BankBonus;
    expect([done, b].sort(bankOrder).map((x) => x.id)).toEqual([1, 2]);
  });
});
