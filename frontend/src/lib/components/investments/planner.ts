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

// The entry for `k` years from today in a by-year list that stops once it stops changing.
const atYear = (list: number[] | undefined, k: number) => (list?.length ? list[Math.min(Math.max(0, k), list.length - 1)] : undefined);

/** Whether a loan's balance is projected (paid down on its terms) rather than held at today's. */
export const loanProjected = (a: PlanAsset) => !!a.loan && a.loan.note == null && a.loan.payment != null;

/** Selling an asset in `year`, in today's dollars: what it's worth then (its value grown by its own yearly change less
 *  inflation; for equity, what will have vested by then) and what's still owed on it. A loan with known terms is paid
 *  down to its balance that year, in today's dollars like the rest; one without stays at today's balance (a
 *  conservative guess). */
export function sale(a: PlanAsset, year: number, thisYear: number, inflation: number): { value: number; owed: number } {
  const k = year - thisYear;
  const real = (1 + a.yearly_change) / (1 + inflation) - 1;
  const value = (atYear(a.value_by_year, k) ?? a.value) * Math.pow(1 + real, k);
  const owed = loanProjected(a) ? (atYear(a.owed_by_year, k) ?? a.owed) / Math.pow(1 + inflation, Math.max(0, k)) : a.owed;
  return { value, owed };
}

/** What selling an asset in `year` brings in, in today's dollars: what it's worth then less what's still owed. */
export function saleProceeds(a: PlanAsset, year: number, thisYear: number, inflation: number): number {
  const { value, owed } = sale(a, year, thisYear, inflation);
  return Math.max(0, value - owed);
}

/** The first year the monthly payment on the loan against an asset is no longer spent: the year after its last
 *  payment (runway/loans.py works that out), or the year the asset is sold if that's sooner. null when its payment
 *  isn't known, or it never ends. */
export function paymentEnds(a: PlanAsset, sellYear: number | null): number | null {
  if (!a.loan?.payment) return null;
  const paid = a.loan.payoff_year;
  const ends = [paid == null ? null : paid + 1, sellYear].filter((y): y is number => y != null);
  return ends.length ? Math.min(...ends) : null;
}

/** Each loan's yearly payment and the first year it's no longer spent (once per loan, however many assets it's
 *  against: the soonest). Only loans whose payment is known, ends, and was counted in the spending the plan starts
 *  from: one categorized as a transfer was never in it, so there's nothing to take off. */
export interface EndingPayment { yearly: number; from: number; name: string; sold: boolean }
export function endingPayments(plan: RetirementPlan, assets: PlanAsset[]): EndingPayment[] {
  const sold = new Map(plan.assets.map((s) => [s.key, s.sell_year]));
  const byLoan = new Map<string, EndingPayment>();
  for (const a of assets) {
    const sellYear = sold.get(a.key) ?? null;
    const from = paymentEnds(a, sellYear);
    const l = a.loan;
    if (from == null || !l?.account_id || !l.payment || !l.payment_counted) continue;
    const had = byLoan.get(l.account_id);
    if (!had || from < had.from) byLoan.set(l.account_id, { yearly: 12 * l.payment, from, name: a.name, sold: from === sellYear });
  }
  return [...byLoan.values()];
}

/** Which dollars the planner shows its figures in: today's (what the projection runs in) or each year's own. */
export type Dollars = "today" | "future";

/** A figure for `year`, given in today's dollars, in the dollars chosen: as it is, or grown by `inflation` a year to
 *  that year's own (future, nominal) dollars. The one place the planner converts: the projection itself stays in
 *  today's dollars, and only what's shown changes. */
export function inDollars(v: number, year: number, thisYear: number, inflation: number, dollars: Dollars): number {
  return dollars === "future" ? v * Math.pow(1 + inflation, year - thisYear) : v;
}

/** The projection's dollar figures, each year's in the dollars chosen (the odds, ages and years don't change). */
export function projectionIn(p: Projection, thisYear: number, inflation: number, dollars: Dollars): Projection {
  if (dollars === "today") return p;
  const each = (vals: number[]) => vals.map((v, i) => inDollars(v, p.years[i], thisYear, inflation, dollars));
  return {
    ...p, low: each(p.low), mid: each(p.mid), high: each(p.high),
    atRetirement: inDollars(p.atRetirement, p.years[p.retireIndex], thisYear, inflation, dollars),
    atEnd: inDollars(p.atEnd, p.years[p.years.length - 1], thisYear, inflation, dollars),
  };
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
  // Runway's spending figure (from your history) includes the payments on loans counted as spending; once such a loan
  // is paid off or its asset sold, its payment comes off. A figure you typed yourself is taken as it is: it probably
  // leaves the loan out already, and taking it off again would count it twice.
  const ending = plan.spending_own ? [] : endingPayments(plan, assets);
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
