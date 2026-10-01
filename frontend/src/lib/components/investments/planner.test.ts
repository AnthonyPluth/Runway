import { describe, expect, it } from "vitest";
import { amortize, flows, paymentEnds, payoffYear, project, RUNS, saleProceeds } from "./planner";
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

// A loan whose payment isn't known: today's balance is the guess.
const house: PlanAsset = {
  key: "home:1", name: "House", kind: "home", value: 300_000, yearly_change: 0.04, owed: 50_000, owed_as_of: `${Y}-10-01`,
  loan_id: "mtg", loan_rate: null, loan_payment: null, payment_counted: true,
};
// The same with its payment: $50,000 at 6% and $1,000 a month is 58 payments, the last in August 2031.
const paying: PlanAsset = { ...house, loan_rate: 0.06, loan_payment: 1_000 };
const byHand = (owed: number, months: number) => { for (let i = 0; i < months; i++) owed = Math.max(0, owed * 1.005 - 1_000); return owed; };

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
    expect(net[4]).toBeCloseTo(267_396.90, 2);
    expect(net.filter((_, i) => i !== 4)).toEqual(Array(10).fill(0));   // an asset no longer on Net worth is skipped
  });

  it("takes a loan's payment off spending from the year after it's paid off", () => {
    // Retires in Y+5 (2031), the year of the last payment: $20,000 then, $8,000 from 2032 on.
    expect(flows(plan(), Y, [paying]).net).toEqual([10_000, 10_000, 10_000, 10_000, 10_000, -20_000, -8_000, -8_000, -8_000, -8_000, -8_000]);
    // no payment known, or a payment that never pays it down: spending as entered
    expect(flows(plan(), Y, [house]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    expect(flows(plan(), Y, [{ ...paying, loan_payment: 200 }]).net.slice(5)).toEqual(Array(6).fill(-20_000));
  });

  it("leaves spending alone when the payment wasn't counted in it (a transfer)", () => {
    const transfer = { ...paying, payment_counted: false };
    expect(flows(plan(), Y, [transfer]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    expect(flows(plan({ assets: [{ key: "home:1", sell_year: Y + 3 }] }), Y, [transfer]).net.slice(5)).toEqual(Array(6).fill(-20_000));
  });

  it("or from the year its asset is sold, if that's sooner", () => {
    const p = plan({ assets: [{ key: "home:1", sell_year: Y + 3 }] });
    const net = flows(p, Y, [paying]).net;
    expect(net.slice(5)).toEqual(Array(6).fill(-8_000));
    expect(net[3]).toBeCloseTo(10_000 + saleProceeds(paying, Y + 3, Y, 0.03), 6);
    // never charges less than nothing, and a loan against two assets comes off once
    expect(flows(plan(), Y, [{ ...paying, loan_payment: 3_000 }]).net.slice(5)).toEqual(Array(6).fill(0));   // paid off in 2028
    expect(flows(plan(), Y, [paying, { ...paying, key: "home:2" }]).net.slice(6)).toEqual(Array(5).fill(-8_000));
  });

  it("still has this year when the plan's end age has passed", () => {
    const f = flows(plan({ plan_to_age: 50 }), Y, []);
    expect(f.years).toEqual([Y]);
    expect(f.retireIndex).toBe(0);
  });
});

describe("loans", () => {
  it("amortize month by month, as by hand", () => {
    expect(amortize(50_000, 0.06, 1_000, 12)).toBeCloseTo(byHand(50_000, 12), 6);
    expect(amortize(50_000, 0.06, 1_000, 0)).toBe(50_000);
    expect(amortize(50_000, 0.06, 1_000, 600)).toBe(0);           // never below zero
    expect(amortize(50_000, 0, 1_000, 12)).toBe(38_000);          // no rate: straight-line
    expect(amortize(50_000, 0.06, 250, 12)).toBe(50_000);         // only the interest: it doesn't shrink, or grow
  });

  it("know the year of the last payment", () => {
    expect(payoffYear(paying)).toBe(2031);
    // from October 1: the first payment on November 1
    expect(payoffYear({ ...paying, loan_rate: 0, owed: 2_000 })).toBe(2026);    // 2 payments: the last in December 2026
    expect(payoffYear({ ...paying, loan_rate: 0, owed: 14_000 })).toBe(2027);   // 14: December 2027
    expect(payoffYear({ ...paying, loan_rate: 0, owed: 14_001 })).toBe(2028);   // 15: January 2028
    expect(payoffYear(house)).toBeNull();                                        // no payment known
    expect(payoffYear({ ...paying, loan_payment: 250 })).toBeNull();             // never paid down
  });

  it("stop being spent the year after the last payment, or when the asset is sold", () => {
    expect(paymentEnds(paying, null)).toBe(2032);
    expect(paymentEnds(paying, Y + 2)).toBe(Y + 2);
    expect(paymentEnds(paying, Y + 9)).toBe(2032);
    expect(paymentEnds({ ...paying, loan_payment: 250 }, null)).toBeNull();
    expect(paymentEnds({ ...paying, loan_payment: 250 }, Y + 9)).toBe(Y + 9);
    expect(paymentEnds(house, Y + 2)).toBeNull();
  });
});

describe("saleProceeds", () => {
  it("grows the value by its yearly change less inflation, then takes off the loan in today's dollars", () => {
    // 300k × (1.04 / 1.03)^4 − 50k / 1.03^4: the balance is in dollars of then
    expect(saleProceeds(house, Y + 4, Y, 0.03)).toBeCloseTo(267_396.90, 2);
  });

  it("pays the loan down to the sale when its payment is known", () => {
    expect(saleProceeds(paying, Y + 4, Y, 0.03)).toBeCloseTo(311_821.25 - byHand(50_000, 48) / Math.pow(1.03, 4), 2);
    expect(saleProceeds(paying, Y + 6, Y, 0.03)).toBeCloseTo(300_000 * Math.pow(1.04 / 1.03, 6), 6);   // paid off by then
  });

  it("is today's equity when sold this year", () => {
    expect(saleProceeds(house, Y, Y, 0.03)).toBe(250_000);
    expect(saleProceeds(paying, Y, Y, 0.03)).toBe(250_000);
  });

  it("keeps pace exactly when it rises with inflation", () => {
    expect(saleProceeds({ ...house, yearly_change: 0.03, owed: 0 }, Y + 10, Y, 0.03)).toBeCloseTo(300_000, 6);
  });

  it("never brings in less than nothing", () => {
    expect(saleProceeds({ ...house, value: 20_000, yearly_change: -0.1, owed: 25_000 }, Y + 2, Y, 0.03)).toBe(0);
  });
});
