// The retirement planner's projection: a Monte Carlo simulation of the plan, year by year, in today's dollars
// (returns are after inflation). Each run draws a return for every year; the middle half of a thousand runs is the
// likely range, and the share of runs that never run dry is the chance the money lasts. The draws come from a fixed
// seed, so the same plan always shows the same picture and typing a figure moves it only by what you changed.
// What you enter (spending, savings, income, events) is in today's dollars and keeps pace with inflation; a loan's
// payment and balance are fixed dollar amounts, so in today's dollars they shrink each year by inflation.
import { fmt0 } from "$lib/format";
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
 *  payment (runway/domain/loans.py works that out), or the year the asset is sold if that's sooner. null when its payment
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
interface LoanPayment { yearly: number; from: number | null; name: string; sold: boolean; counted: boolean | null }
function loanPayments(plan: RetirementPlan, assets: PlanAsset[], thisYear: number): LoanPayment[] {
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
type EndingPayment = LoanPayment & { from: number };
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


// ------------------------------------------------------------------------------------------------ the plan as a form
// What the planner's form needs to make of a plan and its fields: numbers as typed, the ages that can't be, and the
// plain-words lines about the plan, a sale and a loan's payment.

/** Numbers as typed: an empty or half-typed box counts as nothing rather than breaking the projection. */
export const num = (v: unknown) => (typeof v === "number" && isFinite(v) ? v : 0);
/** A fraction as the percentage a field shows ("0.025" → "2.5"). */
export const pctIn = (v: number) => String(+(v * 100).toFixed(2));
export const copyPlan = (p: RetirementPlan): RetirementPlan => JSON.parse(JSON.stringify(p));

/** Runway's own starting figures (what planner.default() gives), from what it knows of your investments. */
export const startingPlan = (computed: { annual_spending: number; yearly_savings: number; expected_return: number }, year: number): Partial<RetirementPlan> => ({
  people: [{ name: "You", birth_year: year - 40, retire_age: 65, savings: computed.yearly_savings }],
  plan_to_age: 95, spending: computed.annual_spending, spending_own: false, return_before: computed.expected_return,
  return_after: 0.04, volatility: 0.12, inflation: 0.025, income: [], events: [], assets: [],
});

type Person = RetirementPlan["people"][number];
/** Who's in the plan, by name ("You" for the first, "Partner" for the second, until they're named). */
export const personNames = (plan: RetirementPlan) => plan.people.map((p, i) => p.name || (i ? "Partner" : "You"));
/** A partner to add: the first person's birth year and retirement age, with nothing saved yet. */
export const newPartner = (plan: RetirementPlan): Person => ({ name: "Partner", birth_year: plan.people[0].birth_year, retire_age: plan.people[0].retire_age, savings: 0 });

// Ages that can't be: retiring before the age you are now, or planning to an age before retirement. Each says so
// under its field, and there's no projection until they're fixed.
const ageNow = (p: Person, year: number) => year - num(p.birth_year);
export const retireError = (p: Person, year: number) =>
  num(p.birth_year) > 1900 && num(p.retire_age) > 0 && num(p.retire_age) < ageNow(p, year) ? `At least ${ageNow(p, year)}, your age now` : null;
// How long to plan for is in the first person's age, like the chart's.
export const planToError = (plan: RetirementPlan) => {
  const retireAt = num(plan.people[0].retire_age);
  return num(plan.plan_to_age) > 0 && retireAt > 0 && num(plan.plan_to_age) <= retireAt ? `Past the retirement age (${retireAt})` : null;
};
export const agesFit = (plan: RetirementPlan, year: number) => plan.people.every((p) => !retireError(p, year)) && !planToError(plan);
/** Whether there's enough to project from: birth years, retirement ages and how long to plan for, all fitting together. */
export const planUsable = (plan: RetirementPlan, year: number) =>
  plan.people.every((p) => num(p.birth_year) > 1900 && num(p.retire_age) > 0) && num(plan.plan_to_age) > 0 && agesFit(plan, year);

/** The plan with every figure a number, as project() takes it (an income or event of a person who's gone is left out). */
export function projectable(plan: RetirementPlan): RetirementPlan {
  const p = copyPlan(plan);
  p.people = p.people.map((x) => ({ ...x, savings: num(x.savings) }));
  p.spending = num(p.spending);
  p.income = p.income.filter((i) => i.person < p.people.length).map((i) => ({ ...i, amount: num(i.amount), start_age: num(i.start_age),
    end_age: typeof i.end_age === "number" && isFinite(i.end_age) ? i.end_age : null }));
  p.events = p.events.map((e) => ({ ...e, amount: num(e.amount), year: num(e.year) }));
  return p;
}

/** Whether a birth year is one a person alive today could have (14 to 100 years ago). */
export const bornOk = (y: unknown, thisYear: number) => num(y) >= thisYear - 100 && num(y) <= thisYear - 14;
/** The Assumptions line once folded: who, how long, income in retirement, returns and inflation. */
export function assumptionsLine(plan: RetirementPlan): string {
  const names = personNames(plan);
  const born = plan.people.length > 1 ? plan.people.map((p, i) => `${names[i]} born ${p.birth_year}`).join(", ") : `Born ${plan.people[0].birth_year}`;
  const income = plan.income.map((inc) => `${inc.name || "Income"} ${fmt0(num(inc.amount))} a year at ${inc.start_age}`);
  const returns = `${pctIn(num(plan.return_before))}% returns (${pctIn(num(plan.return_after))}% retired), ${pctIn(num(plan.inflation))}% inflation`;
  return [born, `to age ${plan.plan_to_age}`, ...(income.length ? income : ["no retirement income yet"]), returns].join(" · ");
}

/** The chance the money lasts, in a word, by the same thresholds as its color; the thresholds are in its tooltip. */
export const verdict = (s: number) => (s >= 0.85 ? "Likely" : s >= 0.7 ? "Uncertain" : "Unlikely");
export const VERDICT_TIP = "Likely at 85% or more, uncertain from 70%, unlikely below 70%";

/** Equity still vesting: what it comes to once it's all vested, at today's share price (value_by_year's last entry). */
export const vestsTo = (a: PlanAsset) => (a.kind === "equity" && (a.value_by_year?.length ?? 0) > 1 ? a.value_by_year![a.value_by_year!.length - 1] : null);

const PAYMENT_FROM = { plaid: "from Plaid", manual: "as you set it", inferred: "from recent payments" } as const;
/** What a sale's estimate assumes, for its tooltip: "Home worth $X in 2057, less $Y still owed on the loan at 6.25%".
 * In future dollars each figure is the sale year's: the home's value grown at its own rate, the loan's balance then. */
export function saleTitle(a: PlanAsset, sellYear: number, thisYear: number, plan: RetirementPlan, dollars: Dollars): string {
  const shownIn = (v: number, y: number) => inDollars(v, y, thisYear, num(plan.inflation), dollars);
  const real = sale(a, sellYear, thisYear, plan.inflation);
  const value = shownIn(real.value, sellYear), owed = shownIn(real.owed, sellYear);
  const worth = a.kind === "equity" ? `${a.name}: ${fmt0(value)} vested by ${sellYear}, at today’s share price${dollars === "future" ? " grown with inflation" : ""}`
    : `${a.name} worth ${fmt0(value)} in ${sellYear}`;
  const l = a.loan;
  let loan = "";
  if (l && loanProjected(a)) {
    const terms = `${+(l.rate ?? 0).toFixed(3)}% and ${fmt0(l.payment ?? 0)} a month ${PAYMENT_FROM[l.source ?? "manual"]}`;
    loan = owed > 0 ? `, less ${fmt0(owed)} still owed on the loan at ${terms}` : `; the loan (${terms}) is paid off by then`;
  } else if (a.owed) {   // held at today's balance in today's dollars, so it grows with inflation in future ones
    loan = dollars === "future" ? `, less the ${fmt0(a.owed)} owed on the loan today (${fmt0(owed)} in ${sellYear} dollars)`
      : `, less ${fmt0(owed)} owed on the loan today`;
  }
  const unit = dollars === "future" ? `In ${sellYear} dollars, at ${pctIn(num(plan.inflation))}% a year inflation.` : "In today’s dollars.";
  return `${worth}${loan}. ${unit}`;
}

/** What happens to a loan's monthly payment in the plan, in plain words: whether it's already in the spending figure
 * and when it stops. */
export function paymentLine(a: PlanAsset, sellYear: number | null, spendingOwn: boolean): string {
  const l = a.loan!;
  const pay = `${fmt0(l.payment ?? 0)}/month`;
  const ends = paymentEnds(a, sellYear);
  const when = ends == null ? null : l.payoff_year == null || ends <= l.payoff_year ? `until it’s sold in ${ends}` : `until it’s paid off in ${l.payoff_year}`;
  const why = l.note === "payment_below_interest" ? " It doesn’t cover the interest, so it never pays the loan down."
    : l.note === "no_rate" ? " Add the loan’s interest rate in Settings → Accounts to see when it ends." : "";
  if (spendingOwn) {
    return `Its ${pay} loan payment goes on ${when ?? "past the end of the plan"}.${why}`;
  }
  if (l.payment_counted) {
    return when ? `Its ${pay} loan payment is already in your spending, ${when}; from ${ends} the plan takes it off.${why}`
      : `Its ${pay} loan payment is already in your spending, and stays in it.${why}`;
  }
  if (l.payment_counted == null) {
    return `Runway couldn’t tell whether its ${pay} loan payment is in your spending, so the plan leaves your spending as it is.${why}`;
  }
  return `Its ${pay} loan payment was paid as a transfer, so it isn’t in your spending and the plan adds it to your spending in retirement ${when ?? "for as long as the plan runs"}.${why}`;
}
