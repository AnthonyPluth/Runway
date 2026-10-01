// The retirement planner's projection: a Monte Carlo simulation of the plan, year by year, in today's dollars
// (returns are after inflation). Each run draws a return for every year; the middle half of a thousand runs is the
// likely range, and the share of runs that never run dry is the chance the money lasts. The draws come from a fixed
// seed, so the same plan always shows the same picture and typing a figure moves it only by what you changed.
// What you enter (spending, savings, income, events) is in today's dollars and keeps pace with inflation; a loan's
// payment and balance are fixed dollar amounts, so in today's dollars they shrink each year by inflation.
import type { PlanAsset, RetirementPlan } from "./types";

export const RUNS = 1000;

/** What the plan holds outside its investments, by kind: homes, other assets and company equity. */
export type HeldKind = "home" | "other" | "equity";
export const HELD_KINDS: HeldKind[] = ["home", "other", "equity"];

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
  held: Record<HeldKind, number[]>;   // each year's home equity, other assets and vested equity not yet sold (held())
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

/** Whether the plan counts an asset: vehicles lose value, so they're neither held nor sold into it (the payment on a
 *  loan against one still is, until it's paid off). Nor are loans against nothing (a student or personal loan): debts,
 *  listed for their payment. */
export const counted = (a: PlanAsset) => a.kind !== "vehicle" && a.kind !== "loan";

/** The year a sale is counted in: never before this one. A sale kept for a year that's now past counts this year. */
export const saleYear = (sellYear: number, thisYear: number) => Math.max(thisYear, sellYear);

/** Whether a loan's balance is projected (paid down on its terms) rather than held at today's. */
export const loanProjected = (a: PlanAsset) => !!a.loan && a.loan.note == null && a.loan.payment != null;

/** Selling an asset in `year`, in today's dollars: what it's worth then and what's still owed on it. Its value grows
 *  by its own yearly change less inflation; with no yearly change set (and for equity, at today's share price) it
 *  keeps pace with inflation, so it holds its value in today's dollars. A loan with known terms is paid down to its
 *  balance that year, in today's dollars like the rest; one without stays at today's balance (a conservative
 *  guess). */
export function sale(a: PlanAsset, year: number, thisYear: number, inflation: number): { value: number; owed: number } {
  const k = year - thisYear;
  const real = a.yearly_change == null ? 0 : (1 + a.yearly_change) / (1 + inflation) - 1;
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

/** A loan's payment in the plan, once per loan however many assets it's against: `yearly` is twelve monthly payments
 *  (a fixed dollar amount), `from` the first year it's no longer paid (the soonest, null when it goes on past the
 *  plan), `counted` whether it's in the spending figure the plan starts from. One that is comes off spending from
 *  `from`; one paid as a transfer instead (false) is added to spending until then, as it's still being paid; one Runway
 *  couldn't find either way (null) is left as it is, as adding one that's already in would count it twice. */
export interface LoanPayment { yearly: number; from: number | null; name: string; sold: boolean; counted: boolean | null }
export function loanPayments(plan: RetirementPlan, assets: PlanAsset[], thisYear: number): LoanPayment[] {
  const byKey = new Map(assets.map((a) => [a.key, a]));
  const sold = new Map(plan.assets.filter((s) => { const a = byKey.get(s.key); return !a || counted(a); })
    .map((s) => [s.key, saleYear(s.sell_year, thisYear)]));
  const byLoan = new Map<string, LoanPayment>();
  const sooner = (a: number | null, b: number | null) => (a ?? Infinity) < (b ?? Infinity);
  for (const a of assets) {
    const l = a.loan;
    if (!l?.account_id || !l.payment) continue;
    const sellYear = sold.get(a.key) ?? null;
    const from = paymentEnds(a, sellYear);
    const had = byLoan.get(l.account_id);
    if (!had || sooner(from, had.from)) {
      byLoan.set(l.account_id, { yearly: 12 * l.payment, from, name: a.name, sold: from != null && from === sellYear, counted: l.payment_counted ?? null });
    }
  }
  return [...byLoan.values()];
}

/** Loan payments in Runway's spending figure that end during the plan, and come off it then. */
export type EndingPayment = LoanPayment & { from: number };
export const endingPayments = (plan: RetirementPlan, assets: PlanAsset[], thisYear: number): EndingPayment[] =>
  loanPayments(plan, assets, thisYear).filter((p): p is EndingPayment => p.counted === true && p.from != null);

/** Loan payments paid as transfers, so missing from Runway's spending figure: added to it while they're still paid. */
export const addedPayments = (plan: RetirementPlan, assets: PlanAsset[], thisYear: number): LoanPayment[] =>
  loanPayments(plan, assets, thisYear).filter((p) => p.counted === false);

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
    held: { home: each(p.held.home), other: each(p.held.other), equity: each(p.held.equity) },
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
  // Runway's spending figure (from your history) includes the payments on loans counted as spending: a fixed dollar
  // amount, so in today's dollars each year it's worth less, and once the loan is paid off or its asset sold it comes
  // off altogether. One paid as a transfer is added while it's still paid; one Runway can't place is left as it is. A
  // figure you typed yourself is
  // taken as it is: it probably has your loans in or out as you mean them, and adjusting it would count them twice.
  const payments = plan.spending_own ? [] : loanPayments(plan, assets, thisYear);
  const deflate = (y: number) => Math.pow(1 + plan.inflation, -Math.max(0, y - thisYear));
  const spending = (y: number) => Math.max(0, plan.spending + payments.reduce((s, p) => {
    const paying = p.from == null || y < p.from;
    if (p.counted == null) return s;
    return s + (paying ? p.yearly * deflate(y) : 0) - (p.counted ? p.yearly : 0);
  }, 0));
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
      const at = saleYear(s.sell_year, thisYear);
      if (a && counted(a) && at === y) f += saleProceeds(a, at, thisYear, plan.inflation);
    }
    return f;
  });
  const retired = years.map((y) => y >= retireYear);
  return { years, ages, net, retired, retireIndex: Math.min(years.length - 1, Math.max(0, retireYear - thisYear)) };
}

/** What the plan holds outside its investments in each of `years`, in today's dollars, by kind: each home's and other
 *  asset's value less what's still owed on it, and equity vested by then. An asset counts until the year it's sold
 *  into the plan; from then its proceeds are in the investments, so it isn't counted twice. Vehicles aren't counted. */
export function held(plan: RetirementPlan, thisYear: number, assets: PlanAsset[], years: number[]): Record<HeldKind, number[]> {
  const sold = new Map(plan.assets.map((s) => [s.key, saleYear(s.sell_year, thisYear)]));
  const out: Record<HeldKind, number[]> = { home: years.map(() => 0), other: years.map(() => 0), equity: years.map(() => 0) };
  for (const a of assets) {
    if (!counted(a)) continue;
    const kind: HeldKind = a.kind === "equity" ? "equity" : a.kind === "home" ? "home" : "other";
    const until = sold.get(a.key) ?? Infinity;
    years.forEach((y, t) => { if (y < until) out[kind][t] += saleProceeds(a, y, thisYear, plan.inflation); });
  }
  return out;
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
    held: held(plan, thisYear, assets, years),
  };
}
