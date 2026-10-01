import { describe, expect, it } from "vitest";
import { flows, project, RUNS, sale, saleProceeds } from "./planner";
import type { PlanAsset, RetirementPlan } from "./types";

const Y = 2026;

// One person, 60 this year, retiring at 65 and planning to 70: eleven years, retired from the sixth.
function plan(over: Partial<RetirementPlan> = {}): RetirementPlan {
  return {
    people: [{ name: "Alex", birth_year: Y - 60, retire_age: 65, savings: 10_000 }],
    plan_to_age: 70,
    spending: 20_000,
    return_before: 0.05,
    return_after: 0.03,
    volatility: 0,
    inflation: 0.03,
    income: [],
    events: [],
    assets: [],
    ...over,
  };
}

const house: PlanAsset = { key: "home:1", name: "House", kind: "home", value: 300_000, yearly_change: 0.04, owed: 50_000 };
// The same house with its loan paid down on known terms: 50k today, 45k, 39k, then paid off in three years.
const paying: PlanAsset = { ...house, owed_by_year: [50_000, 45_000, 39_000, 0],
  loan: { rate: 6.25, payment: 700, source: "manual", note: null } };
// Equity still vesting: 10k vested today, 20k next year, all 40k in two years.
const shares: PlanAsset = { key: "equity:acme", name: "Acme", kind: "equity", value: 10_000, yearly_change: 0, owed: 0,
  value_by_year: [10_000, 20_000, 40_000], owed_by_year: [0], loan: null };

describe("project", () => {
  it("gives the same picture for the same plan", () => {
    const p = plan({ volatility: 0.15 });
    expect(project(p, 250_000, Y, [])).toEqual(project(p, 250_000, Y, []));
  });

  it("with no volatility, compounds exactly as by hand", () => {
    const r = project(plan(), 100_000, Y, []);
    // Year 0: 100k + 10k saved. Years 1-4: ×1.05 + 10k. Year 5 on (retired): ×1.03 − 20k.
    const expected = [110000, 125500, 141775, 158863.75, 176806.94, 162111.15, 146974.48, 131383.71, 115325.23, 98784.98, 81748.53];
    expect(r.years).toEqual(Array.from({ length: 11 }, (_, i) => Y + i));
    expect(r.ages).toEqual([Array.from({ length: 11 }, (_, i) => 60 + i)]);
    r.mid.forEach((v, i) => expect(v).toBeCloseTo(expected[i], 2));
    // Every run is the same, so the likely range collapses to the one path.
    expect(r.low).toEqual(r.mid);
    expect(r.high).toEqual(r.mid);
    expect(r.retireIndex).toBe(5);
    expect(r.atRetirement).toBeCloseTo(162111.15, 2);
    expect(r.atEnd).toBeCloseTo(81748.53, 2);
    expect(r.success).toBe(1);
    expect(r.runsOutAge).toBeNull();
  });

  it("keeps the likely range ordered when markets vary", () => {
    const r = project(plan({ volatility: 0.2 }), 500_000, Y, []);
    r.low.forEach((lo, i) => {
      expect(lo).toBeLessThanOrEqual(r.mid[i]);
      expect(r.mid[i]).toBeLessThanOrEqual(r.high[i]);
    });
    expect(r.high.at(-1)).toBeGreaterThan(r.low.at(-1)!);
  });

  it("succeeds every time for a trivially funded plan, and never for an impossible one", () => {
    expect(project(plan({ volatility: 0.2 }), 10_000_000, Y, []).success).toBe(1);
    const broke = plan({ volatility: 0.2, people: [{ name: "Alex", birth_year: Y - 65, retire_age: 65, savings: 0 }], spending: 1_000_000 });
    expect(project(broke, 0, Y, []).success).toBe(0);
  });

  it("counts the share of runs that last", () => {
    const r = project(plan({ volatility: 0.25 }), 150_000, Y, [], 200);
    expect(r.success).toBeGreaterThan(0);
    expect(r.success).toBeLessThan(1);
    expect(r.success * 200).toBe(Math.round(r.success * 200));
    expect(RUNS).toBe(1000);
  });

  it("finds the age the money runs out", () => {
    // Retired already, nothing coming in: 100k → 70k → 40k → 10k → gone at 63.
    const p = plan({ people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }], spending: 30_000, return_after: 0 });
    const r = project(p, 100_000, Y, []);
    expect(r.mid.slice(0, 5)).toEqual([70_000, 40_000, 10_000, 0, 0]);
    expect(r.runsOutAge).toBe(63);
    expect(r.lowRunsOutAge).toBe(63);
    expect(r.success).toBe(0);
  });

  it("doesn't call an empty year before retirement running out", () => {
    // Nothing saved until a windfall the year you retire: zeros before then are only years of saving.
    const p = plan({
      people: [{ name: "Alex", birth_year: Y - 60, retire_age: 62, savings: 0 }],
      spending: 1_000, events: [{ name: "Inheritance", year: Y + 2, amount: 1_000_000 }],
    });
    const r = project(p, 0, Y, []);
    expect(r.mid.slice(0, 2)).toEqual([0, 0]);
    expect(r.runsOutAge).toBeNull();
    expect(r.success).toBe(1);
  });
});

describe("flows", () => {
  it("stops each person's savings at their own retirement, and spends only once everyone has retired", () => {
    const p = plan({
      people: [
        { name: "Alex", birth_year: Y - 60, retire_age: 62, savings: 1_000 },   // retires in Y+2
        { name: "Sam", birth_year: Y - 55, retire_age: 60, savings: 100 },      // retires in Y+5
      ],
      plan_to_age: 67,
      spending: 5_000,
    });
    const f = flows(p, Y, []);
    expect(f.net.slice(0, 7)).toEqual([1_100, 1_100, 100, 100, 100, -5_000, -5_000]);
    expect(f.retired.slice(0, 7)).toEqual([false, false, false, false, false, true, true]);
    expect(f.retireIndex).toBe(5);
    // The plan runs until the younger one reaches 67.
    expect(f.years.at(-1)).toBe(Y - 55 + 67);
  });

  it("pays income only from its start age until its end age", () => {
    const p = plan({
      people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }],
      spending: 0,
      income: [
        { name: "Consulting", amount: 7_000, person: 0, start_age: 62, end_age: 65 },
        { name: "Pension", amount: 2_000, person: 0, start_age: 64, end_age: null },
        { name: "Nobody's", amount: 999, person: 3, start_age: 0, end_age: null },
      ],
    });
    // Ages 60..70: consulting at 62, 63 and 64; the pension from 64 on.
    expect(flows(p, Y, []).net).toEqual([0, 0, 7_000, 7_000, 9_000, 2_000, 2_000, 2_000, 2_000, 2_000, 2_000]);
  });

  it("puts one-time events in their year", () => {
    const p = plan({ spending: 0, people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }],
      events: [{ name: "Roof", year: Y + 3, amount: -20_000 }, { name: "Gift", year: Y + 3, amount: 5_000 }] });
    const net = flows(p, Y, []).net;
    expect(net[3]).toBe(-15_000);
    expect(net.filter((x, i) => i !== 3 && x !== 0)).toEqual([]);
  });

  it("adds what a sold asset brings in, in the year it's sold", () => {
    const p = plan({ spending: 0, people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }],
      assets: [{ key: "home:1", sell_year: Y + 4 }, { key: "gone", sell_year: Y + 1 }] });
    const net = flows(p, Y, [house]).net;
    expect(net[4]).toBeCloseTo(261_821.25, 2);
    expect(net.filter((_, i) => i !== 4)).toEqual(Array(10).fill(0));   // an asset no longer on Net worth is skipped
  });

  it("counts a loan paid down and equity vested by the year they're sold", () => {
    const p = plan({ spending: 0, people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }], inflation: 0,
      assets: [{ key: "home:1", sell_year: Y + 2 }, { key: "equity:acme", sell_year: Y + 5 }] });
    const net = flows(p, Y, [{ ...paying, yearly_change: 0 }, shares]).net;
    expect(net[2]).toBe(300_000 - 39_000);
    expect(net[5]).toBe(40_000);
  });

  it("still has this year when the plan's end age has passed", () => {
    const f = flows(plan({ plan_to_age: 50 }), Y, []);
    expect(f.years).toEqual([Y]);
    expect(f.retireIndex).toBe(0);
  });
});

describe("saleProceeds", () => {
  it("grows the value by its yearly change less inflation, then takes off the loan", () => {
    // 300k × (1.04 / 1.03)^4 − 50k
    expect(saleProceeds(house, Y + 4, Y, 0.03)).toBeCloseTo(261_821.25, 2);
  });

  it("is today's equity when sold this year", () => {
    expect(saleProceeds(house, Y, Y, 0.03)).toBe(250_000);
  });

  it("keeps pace exactly when it rises with inflation", () => {
    expect(saleProceeds({ ...house, yearly_change: 0.03 }, Y + 10, Y, 0.03)).toBeCloseTo(250_000, 6);
  });

  it("never brings in less than nothing", () => {
    expect(saleProceeds({ ...house, value: 20_000, yearly_change: -0.1, owed: 25_000 }, Y + 2, Y, 0.03)).toBe(0);
  });

  it("takes off the loan's projected balance that year, in today's dollars", () => {
    // 300k × (1.04 / 1.03)^2 − 39k / 1.03^2
    expect(saleProceeds(paying, Y + 2, Y, 0.03)).toBeCloseTo(300_000 * (1.04 / 1.03) ** 2 - 39_000 / 1.03 ** 2, 6);
    expect(saleProceeds(paying, Y, Y, 0.03)).toBe(250_000);
  });

  it("owes nothing once the loan is paid off, however long after", () => {
    expect(sale(paying, Y + 3, Y, 0).owed).toBe(0);
    expect(sale(paying, Y + 30, Y, 0)).toEqual({ value: 300_000 * 1.04 ** 30, owed: 0 });
  });

  it("keeps today's balance when the loan can't be projected", () => {
    for (const note of ["no_rate", "no_payment", "payment_below_interest"] as const) {
      const a = { ...house, owed_by_year: [50_000], loan: { rate: null, payment: null, source: null, note } };
      expect(sale(a, Y + 4, Y, 0.03).owed).toBe(50_000);
      expect(saleProceeds(a, Y + 4, Y, 0.03)).toBeCloseTo(261_821.25, 2);
    }
  });

  it("counts equity vested by the year it's sold, at today's price less inflation", () => {
    expect(saleProceeds(shares, Y, Y, 0.03)).toBe(10_000);
    expect(saleProceeds(shares, Y + 1, Y, 0.03)).toBeCloseTo(20_000 / 1.03, 6);
    expect(saleProceeds(shares, Y + 10, Y, 0)).toBe(40_000);   // fully vested from the third year
  });
});
