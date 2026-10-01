// The retirement planner's projection: a Monte Carlo simulation of the plan, year by year, in today's dollars
// (returns are after inflation). Each run draws a return for every year; the middle half of a thousand runs is the
// likely range, and the share of runs that never run dry is the chance the money lasts. The draws come from a fixed
// seed, so the same plan always shows the same picture and typing a figure moves it only by what you changed.
import type { PlanAsset, RetirementPlan } from "./types";

export const RUNS = 1000;

export interface Projection {
  years: number[];                 // calendar years, this one first
  ages: number[][];                // each person's age in each year
  low: number[]; mid: number[]; high: number[];   // 25th, 50th and 75th percentile of what's invested: the likely range
  success: number;                 // share of runs where the money lasted to the end of the plan
  retireIndex: number;             // the year everyone has retired
  atRetirement: number;            // median invested then
  atEnd: number;                   // median left at the end
  runsOutAge: number | null;       // your age when the median run runs out, if it does
  lowRunsOutAge: number | null;    // the same in poor markets (25th percentile)
}

// A small, fast seeded generator (mulberry32), and normally distributed draws from it (Box–Muller).
function rng(seed: number) {
  let a = seed >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return () => Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
}

const quantile = (sorted: Float64Array, q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];

/** What's left of a loan after `months` monthly payments, at `rate` a year (0: straight-line), as runway/networth.py
 *  works it out. A payment that doesn't cover the interest leaves it where it is, and it never goes below zero. */
export function amortize(owed: number, rate: number, payment: number, months: number): number {
  if (owed <= 0 || !payment || months <= 0) return owed;
  const r = rate / 12;
  if (payment <= owed * r) return owed;
  if (r === 0) return Math.max(0, owed - payment * months);
  const growth = Math.pow(1 + r, months);
  return Math.max(0, owed * growth - (payment * (growth - 1)) / r);
}

/** The calendar year of the last payment on the loan against an asset, counting a payment a month from `owed_as_of`;
 *  null when its payment isn't known or doesn't pay it down. */
export function payoffYear(a: PlanAsset): number | null {
  if (!a.loan_payment) return null;
  const [y, m] = a.owed_as_of.split("-").map(Number);
  if (a.owed <= 0) return y;
  const r = (a.loan_rate ?? 0) / 12;
  if (a.loan_payment <= a.owed * r) return null;
  // Payments until nothing's left; the tiny allowance keeps an exact fit from rounding up a month.
  const n = r === 0 ? a.owed / a.loan_payment : -Math.log(1 - (a.owed * r) / a.loan_payment) / Math.log(1 + r);
  return y + Math.floor((m - 1 + Math.ceil(n - 1e-9)) / 12);
}

/** The first year the loan's monthly payment is no longer spent: the year after it's paid off, or the year the asset
 *  is sold if that's sooner. null when there's no payment known, or it never ends. */
export function paymentEnds(a: PlanAsset, sellYear: number | null): number | null {
  if (!a.loan_payment) return null;
  const paid = payoffYear(a);
  const ends = [paid == null ? null : paid + 1, sellYear].filter((y): y is number => y != null);
  return ends.length ? Math.min(...ends) : null;
}

/** What selling an asset in `year` brings in, in today's dollars: its value grown by its own yearly change less
 *  inflation, minus the loan against it. With its monthly payment known the loan is paid down to the sale (a year
 *  of payments for each year from now); without, today's balance is the guess. Either way the balance is in dollars of
 *  that year, so it's taken back to today's by inflation like everything else. */
export function saleProceeds(a: PlanAsset, year: number, thisYear: number, inflation: number): number {
  const t = year - thisYear;
  const real = (1 + a.yearly_change) / (1 + inflation) - 1;
  const owed = a.loan_payment ? amortize(a.owed, a.loan_rate ?? 0, a.loan_payment, 12 * t) : a.owed;
  return Math.max(0, a.value * Math.pow(1 + real, t) - owed / Math.pow(1 + inflation, t));
}

/** Each loan's yearly payment and the first year it's no longer spent (once per loan, however many assets it's
 *  against: the soonest). Only loans whose payment is known, ends, and was counted in the spending the plan starts
 *  from: one categorized as a transfer was never in it, so there's nothing to take off. */
export function endingPayments(plan: RetirementPlan, assets: PlanAsset[]): { yearly: number; from: number }[] {
  const sold = new Map(plan.assets.map((s) => [s.key, s.sell_year]));
  const byLoan = new Map<string, { yearly: number; from: number }>();
  for (const a of assets) {
    const from = paymentEnds(a, sold.get(a.key) ?? null);
    if (from == null || !a.loan_id || !a.loan_payment || !a.payment_counted) continue;
    const had = byLoan.get(a.loan_id);
    if (!had || from < had.from) byLoan.set(a.loan_id, { yearly: 12 * a.loan_payment, from });
  }
  return [...byLoan.values()];
}

/** Money in and out in each year other than the market: savings, income, spending, one-time events and sales. */
export function flows(plan: RetirementPlan, thisYear: number, assets: PlanAsset[]) {
  const people = plan.people;
  const last = Math.max(...people.map((p) => p.birth_year + plan.plan_to_age));
  const years: number[] = [];
  for (let y = thisYear; y <= Math.max(thisYear, last); y++) years.push(y);
  const ages = people.map((p) => years.map((y) => y - p.birth_year));
  const byKey = new Map(assets.map((a) => [a.key, a]));
  const retireYear = Math.max(...people.map((p) => p.birth_year + p.retire_age));
  // Spending (from your history) includes the payments on loans counted as spending; once such a loan is paid off or
  // its asset sold, its payment comes off. The plan's own spending figure stays as you entered it.
  const ending = endingPayments(plan, assets);
  const spending = (y: number) => Math.max(0, plan.spending - ending.reduce((s, e) => s + (y >= e.from ? e.yearly : 0), 0));
  const net = years.map((y, t) => {
    let f = 0;
    people.forEach((p, i) => { if (ages[i][t] < p.retire_age) f += p.savings; });
    for (const inc of plan.income) {
      const age = ages[inc.person]?.[t];
      if (age != null && age >= inc.start_age && (inc.end_age == null || age < inc.end_age)) f += inc.amount;
    }
    if (y >= retireYear) f -= spending(y);   // until everyone's retired, pay covers living costs
    for (const e of plan.events) if (e.year === y) f += e.amount;
    for (const s of plan.assets) {
      const a = byKey.get(s.key);
      if (a && s.sell_year === y) f += saleProceeds(a, y, thisYear, plan.inflation);
    }
    return f;
  });
  const retired = years.map((y) => y >= retireYear);
  return { years, ages, net, retired, retireIndex: Math.min(years.length - 1, Math.max(0, retireYear - thisYear)) };
}

export function project(plan: RetirementPlan, current: number, thisYear: number, assets: PlanAsset[], runs = RUNS): Projection {
  const { years, ages, net, retired, retireIndex } = flows(plan, thisYear, assets);
  const n = years.length;
  const values = Array.from({ length: n }, () => new Float64Array(runs));
  const draw = rng(20260929);
  let lasted = 0;
  for (let r = 0; r < runs; r++) {
    let v = current, ok = true;
    for (let t = 0; t < n; t++) {
      if (t > 0) {   // this year starts from what you have now; growth counts from next year
        const mean = retired[t] ? plan.return_after : plan.return_before;
        v *= 1 + Math.max(-0.9, mean + plan.volatility * draw());
      }
      v += net[t];
      if (v < 0) { v = 0; ok = false; }
      values[t][r] = v;
    }
    if (ok) lasted++;
  }
  const low: number[] = [], mid: number[] = [], high: number[] = [];
  for (const col of values) {
    col.sort();
    low.push(quantile(col, 0.25)); mid.push(quantile(col, 0.5)); high.push(quantile(col, 0.75));
  }
  // Runs out: the first year after retirement with nothing left (a zero before then is only a year of saving).
  const outAge = (path: number[]) => {
    const t = path.findIndex((v, i) => i >= retireIndex && v <= 0 && net[i] < 0);
    return t < 0 ? null : ages[0][t];
  };
  return {
    years, ages, low, mid, high, success: lasted / runs, retireIndex,
    atRetirement: mid[retireIndex], atEnd: mid[n - 1], runsOutAge: outAge(mid), lowRunsOutAge: outAge(low),
  };
}
