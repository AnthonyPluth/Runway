import { describe, expect, it } from "vitest";
import { addedPayments, endingPayments, flows, held, inDollars, paymentEnds, project, projectionIn, RUNS, sale, saleProceeds } from "./planner";
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
// Equity still vesting: 10k vested today, 20k next year, all 40k in two years, at a share price that keeps pace with inflation.
const shares: PlanAsset = { key: "equity:acme", name: "Acme", kind: "equity", value: 10_000, yearly_change: null, owed: 0,
  value_by_year: [10_000, 20_000, 40_000], owed_by_year: [0], loan: null };
// A loan whose payment is in your spending: $50,000 at 6% and $1,000 a month is 58 payments, the last in August 2031
// (runway/loans.py works out the year).
const repaying: PlanAsset = { ...house, owed_by_year: [50_000, 40_748.33, 30_926.03, 20_497.92, 9_426.63, 0],
  loan: { rate: 6, payment: 1_000, source: "manual", note: null, account_id: "mtg", payoff_year: 2031, payment_counted: true } };
const repay = (loan: Partial<NonNullable<PlanAsset["loan"]>>, over: Partial<PlanAsset> = {}): PlanAsset =>
  ({ ...repaying, ...over, loan: { ...repaying.loan!, ...loan } });

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

  // A loan's payment is a fixed dollar amount: in today's dollars it's worth 1/1.03^k of itself k years out.
  const d = (k: number) => 1.03 ** -k;
  const closeTo = (got: number[], want: number[]) => { expect(got).toHaveLength(want.length); got.forEach((g, i) => expect(g).toBeCloseTo(want[i], 6)); };

  it("takes a payment off Runway's spending figure only, not one you typed (it probably has the loan as you mean it)", () => {
    closeTo(flows(plan({ spending_own: false }), Y, [repaying]).net.slice(5), [-(8_000 + 12_000 * d(5)), -8_000, -8_000, -8_000, -8_000, -8_000]);
    expect(flows(plan({ spending_own: true }), Y, [repaying]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    const sold = plan({ spending_own: true, assets: [{ key: "home:1", sell_year: Y + 3 }] });
    expect(flows(sold, Y, [repaying]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    expect(flows(plan({ spending_own: true }), Y, [repay({ payment_counted: false })]).net.slice(5)).toEqual(Array(6).fill(-20_000));
  });

  it("names each ending payment and why it ends", () => {
    expect(endingPayments(plan(), [repaying], Y)).toEqual([{ yearly: 12_000, from: 2032, name: "House", sold: false, counted: true }]);
    expect(endingPayments(plan({ assets: [{ key: "home:1", sell_year: Y + 3 }] }), [repaying], Y))
      .toEqual([{ yearly: 12_000, from: Y + 3, name: "House", sold: true, counted: true }]);
    expect(endingPayments(plan(), [repay({ payment_counted: false })], Y)).toEqual([]);
    expect(addedPayments(plan(), [repay({ payment_counted: false })], Y)).toEqual([{ yearly: 12_000, from: 2032, name: "House", sold: false, counted: false }]);
    expect(addedPayments(plan(), [repaying], Y)).toEqual([]);
  });

  it("takes a loan's payment off spending from the year after it's paid off", () => {
    // Retires in Y+5 (2031), the year of the last payment: $8,000 and that year's payment then, $8,000 from 2032 on.
    closeTo(flows(plan(), Y, [repaying]).net, [10_000, 10_000, 10_000, 10_000, 10_000, -(8_000 + 12_000 * d(5)), -8_000, -8_000, -8_000, -8_000, -8_000]);
    // without inflation the payment stays what it is: all of it until it ends
    expect(flows(plan({ inflation: 0 }), Y, [repaying]).net.slice(5)).toEqual([-20_000, -8_000, -8_000, -8_000, -8_000, -8_000]);
    // no payment known: spending as entered
    expect(flows(plan(), Y, [house]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    expect(flows(plan(), Y, [repay({ payment: null, note: "no_payment", payoff_year: null })]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    // a payment that never pays it down goes on, worth less each year in today's dollars
    closeTo(flows(plan(), Y, [repay({ payment: 200, note: "payment_below_interest", payoff_year: null })]).net.slice(5),
      [5, 6, 7, 8, 9, 10].map((k) => -(17_600 + 2_400 * d(k))));
  });

  it("adds a payment that wasn't counted in spending (a transfer) while it's still paid", () => {
    const transfer = repay({ payment_counted: false });
    closeTo(flows(plan(), Y, [transfer]).net.slice(5), [-(20_000 + 12_000 * d(5)), -20_000, -20_000, -20_000, -20_000, -20_000]);
    expect(flows(plan({ assets: [{ key: "home:1", sell_year: Y + 3 }] }), Y, [transfer]).net.slice(5)).toEqual(Array(6).fill(-20_000));
    // one that doesn't end is added all along
    closeTo(flows(plan(), Y, [repay({ payment_counted: false, payoff_year: null })]).net.slice(5), [5, 6, 7, 8, 9, 10].map((k) => -(20_000 + 12_000 * d(k))));
  });

  it("or from the year its asset is sold, if that's sooner", () => {
    const p = plan({ assets: [{ key: "home:1", sell_year: Y + 3 }] });
    const net = flows(p, Y, [repaying]).net;
    expect(net.slice(5)).toEqual(Array(6).fill(-8_000));
    expect(net[3]).toBeCloseTo(10_000 + saleProceeds(repaying, Y + 3, Y, 0.03), 6);
    // even one whose payment doesn't cover the interest stops when it's sold
    const short = repay({ payment: 200, note: "payment_below_interest", payoff_year: null });
    expect(flows(p, Y, [short]).net.slice(5)).toEqual(Array(6).fill(-17_600));
    // never charges less than nothing, and a loan against two assets comes off once
    expect(flows(plan(), Y, [repay({ payment: 3_000, payoff_year: 2028 })]).net.slice(5)).toEqual(Array(6).fill(0));
    expect(flows(plan(), Y, [repaying, { ...repaying, key: "home:2" }]).net.slice(6)).toEqual(Array(5).fill(-8_000));
  });

  it("counts a sale kept for a year that's now past this year", () => {
    const p = plan({ spending: 0, people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }],
      assets: [{ key: "home:1", sell_year: Y - 2 }] });
    const net = flows(p, Y, [house]).net;
    expect(net[0]).toBe(250_000);   // today's value less today's loan
    expect(net.slice(1)).toEqual(Array(10).fill(0));
    expect(endingPayments(plan({ assets: [{ key: "home:1", sell_year: Y - 2 }] }), [repaying], Y)[0].from).toBe(Y);
  });

  it("leaves vehicles out: never sold into the plan, but a loan against one is paid until it's paid off", () => {
    const car: PlanAsset = { ...repaying, key: "asset:car", name: "Car", kind: "vehicle", value: 30_000, yearly_change: -0.15 };
    const p = plan({ assets: [{ key: "asset:car", sell_year: Y + 1 }] });
    const net = flows(p, Y, [car]).net;
    expect(net[1]).toBe(10_000);   // no sale
    closeTo(net.slice(5), [-(8_000 + 12_000 * d(5)), -8_000, -8_000, -8_000, -8_000, -8_000]);   // the sale doesn't end its payment either
    expect(held(p, Y, [car], [Y, Y + 1]).home).toEqual([0, 0]);
    expect(held(p, Y, [car], [Y, Y + 1]).other).toEqual([0, 0]);
  });
});

describe("held", () => {
  const years = [Y, Y + 1, Y + 2, Y + 3];
  it("counts each home's equity, other assets and vested equity until they're sold, in today's dollars", () => {
    const cabin: PlanAsset = { key: "asset:cabin", name: "Cabin", kind: "other", value: 80_000, yearly_change: null, owed: 0 };
    const h = held(plan({ assets: [{ key: "home:1", sell_year: Y + 2 }] }), Y, [house, cabin, shares], years);
    expect(h.home.slice(0, 2)).toEqual([250_000, saleProceeds(house, Y + 1, Y, 0.03)]);
    expect(h.home.slice(2)).toEqual([0, 0]);   // sold in Y+2: from then the proceeds are in the investments
    expect(h.other).toEqual([80_000, 80_000, 80_000, 80_000]);   // no yearly change set: level in today's dollars
    expect(h.equity).toEqual([10_000, 20_000, 40_000, 40_000]);   // vesting, at a share price that keeps pace with inflation
  });

  it("counts a home's value less its loan as it's paid down, never less than nothing", () => {
    const h = held(plan({ inflation: 0 }), Y, [{ ...paying, yearly_change: 0 }], years);
    expect(h.home).toEqual([250_000, 255_000, 261_000, 300_000]);
    expect(held(plan(), Y, [{ ...house, value: 10_000 }], [Y]).home).toEqual([0]);
  });

  it("isn't counted twice: what's held stops the year the sale's proceeds go into the investments", () => {
    const p = plan({ spending: 0, inflation: 0, people: [{ name: "Alex", birth_year: Y - 60, retire_age: 60, savings: 0 }],
      return_after: 0, assets: [{ key: "home:1", sell_year: Y + 2 }] });
    const r = project(p, 0, Y, [{ ...house, yearly_change: 0 }]);
    const total = r.mid.map((m, i) => m + r.held.home[i]);
    expect(total.slice(0, 5)).toEqual([250_000, 250_000, 250_000, 250_000, 250_000]);
  });
});

describe("future dollars", () => {
  it("changes only how the projection is shown: the odds, run-out ages and every year's events are the same", () => {
    const p = plan({ volatility: 0.15, spending: 45_000, assets: [{ key: "home:1", sell_year: Y + 7 }],
      income: [{ name: "Social Security", amount: 20_000, person: 0, start_age: 67, end_age: null }],
      events: [{ name: "Roof", year: Y + 2, amount: -15_000 }] });
    const assets = [repaying, repay({ account_id: "car", payment_counted: false }, { key: "asset:car", kind: "vehicle" }), shares];
    const today = project(p, 150_000, Y, assets);
    const future = projectionIn(today, Y, p.inflation, "future");
    expect([future.success, future.runsOutAge, future.lowRunsOutAge, future.retireIndex])
      .toEqual([today.success, today.runsOutAge, today.lowRunsOutAge, today.retireIndex]);
    expect(future.years).toEqual(today.years);
    today.years.forEach((_, k) => {
      const f = 1.03 ** k;
      for (const key of ["low", "mid", "high"] as const) expect(future[key][k]).toBeCloseTo(today[key][k] * f, 6);
      for (const kind of ["home", "other", "equity"] as const) expect(future.held[kind][k]).toBeCloseTo(today.held[kind][k] * f, 6);
      // a value of 0 (run out, or sold) is 0 in either
      expect(future.mid[k] === 0).toBe(today.mid[k] === 0);
    });
  });
});

describe("flows (more)", () => {

  it("still has this year when the plan's end age has passed", () => {
    const f = flows(plan({ plan_to_age: 50 }), Y, []);
    expect(f.years).toEqual([Y]);
    expect(f.retireIndex).toBe(0);
  });
});

describe("a loan's payment", () => {
  it("stops being spent the year after the last payment, or when the asset is sold", () => {
    expect(paymentEnds(repaying, null)).toBe(2032);
    expect(paymentEnds(repaying, Y + 2)).toBe(Y + 2);
    expect(paymentEnds(repaying, Y + 9)).toBe(2032);
    const short = repay({ payment: 250, note: "payment_below_interest", payoff_year: null });
    expect(paymentEnds(short, null)).toBeNull();
    expect(paymentEnds(short, Y + 9)).toBe(Y + 9);
    expect(paymentEnds(house, Y + 2)).toBeNull();   // no loan terms
    expect(paymentEnds(paying, Y + 2)).toBe(Y + 2);  // terms from an older server, without a payoff year: only a sale ends it
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

  it("counts equity vested by the year it's sold, at today's share price keeping pace with inflation", () => {
    expect(saleProceeds(shares, Y, Y, 0.03)).toBe(10_000);
    expect(saleProceeds(shares, Y + 1, Y, 0.03)).toBeCloseTo(20_000, 6);   // level in today’s dollars
    expect(saleProceeds({ ...shares, yearly_change: 0 }, Y + 1, Y, 0.03)).toBeCloseTo(20_000 / 1.03, 6);   // a price held flat would lose value
    expect(saleProceeds(shares, Y + 10, Y, 0)).toBe(40_000);   // fully vested from the third year
  });
});

describe("inDollars", () => {
  it("grows a figure by inflation to its own year's dollars", () => {
    expect(inDollars(100_000, Y + 10, Y, 0.025, "future")).toBeCloseTo(128_008.45, 2);   // 1.025^10 = 1.28008…
    expect(inDollars(100_000, Y, Y, 0.025, "future")).toBe(100_000);   // this year's are today's
  });

  it("leaves today's dollars as they are", () => {
    expect(inDollars(100_000, Y + 10, Y, 0.025, "today")).toBe(100_000);
  });

  it("turns a sale back into that year's figures: the value grown at its own rate, and the loan's balance then", () => {
    const s = sale(paying, Y + 2, Y, 0.03);
    expect(inDollars(s.owed, Y + 2, Y, 0.03, "future")).toBeCloseTo(39_000, 6);
    expect(inDollars(s.value, Y + 2, Y, 0.03, "future")).toBeCloseTo(300_000 * 1.04 ** 2, 6);
    expect(inDollars(sale(shares, Y + 2, Y, 0.03).value, Y + 2, Y, 0.03, "future")).toBeCloseTo(40_000 * 1.03 ** 2, 6);   // today’s share price, grown with inflation
  });
});

describe("projectionIn", () => {
  const p = project(plan({ volatility: 0.1 }), 100_000, Y, []);

  it("is the projection itself in today's dollars", () => {
    expect(projectionIn(p, Y, 0.03, "today")).toBe(p);
  });

  it("puts each year's figures in that year's dollars, and keeps the odds and ages", () => {
    const f = projectionIn(p, Y, 0.03, "future");
    for (const k of [0, 3, 10]) {
      expect(f.mid[k]).toBeCloseTo(p.mid[k] * 1.03 ** k, 6);
      expect(f.low[k]).toBeCloseTo(p.low[k] * 1.03 ** k, 6);
      expect(f.high[k]).toBeCloseTo(p.high[k] * 1.03 ** k, 6);
    }
    expect(f.atRetirement).toBeCloseTo(p.atRetirement * 1.03 ** 5, 6);   // everyone has retired from the sixth year
    expect(f.atEnd).toBeCloseTo(p.atEnd * 1.03 ** 10, 6);
    expect([f.success, f.retireIndex, f.runsOutAge, f.lowRunsOutAge]).toEqual([p.success, p.retireIndex, p.runsOutAge, p.lowRunsOutAge]);
    expect(f.years).toEqual(p.years);
    expect(f.ages).toEqual(p.ages);
  });
});
