// The shapes of the Investments page's API replies (runway/server.py, runway/portfolio.py).

/** One account's share of a holding (the same fund in two accounts is one holding with two lots). */
interface Lot {
  account_id: string;
  security_id: string;
  account_name: string;
  quantity: number;
  value: number;
  cost_basis: number | null;
  reported_cost_basis: number | null;
  per_share: number | null;
  manual: boolean;
}

export interface Holding {
  security_id: string;
  group: string;
  ticker: string | null;
  name: string | null;
  type: string | null;
  asset_class: string;
  sector: string | null;
  is_cash: boolean;
  quantity: number;
  value: number;
  cost_basis: number;
  cost_known: boolean;
  cost_manual: boolean;
  accounts: string[];
  price: number | null;
  lots: Lot[];
  allocation: number;
  gain: number | null;
  gain_pct: number | null;
  day_change: number | null;
  day_change_pct: number | null;
  /** Set by live quotes: priced in the last 20 minutes while the market is open. */
  live?: boolean;
  live_time?: number;
}

export interface InvAccount {
  id: string;
  item_id: string;
  name: string | null;
  official_name: string | null;
  subtype: string | null;
  mask: string | null;
  balance: number | null;
  hidden: number;
  hidden_in_accounts: number;
  source: "plaid" | "simplefin" | string;
  institution_name: string | null;
  tracked: number;
  drift: number | null;
  /** A Plaid account that SimpleFIN also sends (the SimpleFIN copy isn't listed or counted). */
  also_simplefin?: boolean;
  /** A SimpleFIN account that a Plaid one also is (its id): this one is left out of the page. */
  duplicate_of?: string | null;
}

export interface Activity {
  id: string;
  account_id: string;
  date: string;
  name: string | null;
  type: string | null;
  subtype: string | null;
  quantity: number | null;
  price: number | null;
  amount: number | null;
  ticker: string | null;
  account_name: string;
}

interface Performance {
  period?: string;
  start?: string;
  start_value?: number;
  end_value?: number;
  net_deposits?: number;
  gain?: number;
  return?: number;
  benchmark_return?: number | null;
}

interface AllocItem { name: string; value: number; share: number }
export type AllocKey = "asset_class" | "account" | "sector" | "holding";

/** The retirement plan, in today's dollars (runway/planner.py keeps it; planner.ts projects it). */
interface PlanPerson { name: string; birth_year: number; retire_age: number; savings: number }
interface PlanIncome { name: string; amount: number; person: number; start_age: number; end_age: number | null }
interface PlanEvent { name: string; year: number; amount: number }
export interface RetirementPlan {
  people: PlanPerson[];
  plan_to_age: number;
  spending: number;
  return_before: number;
  return_after: number;
  volatility: number;
  inflation: number;
  income: PlanIncome[];
  events: PlanEvent[];
  assets: { key: string; sell_year: number }[];
}
/** What a loan's projection is based on (runway/loans.py): the annual rate in percent, the monthly payment, where
 *  the payment came from, and why it couldn't be projected (today's balance is then used). `account_id` is the loan
 *  account (one loan against two assets is the same loan); `payoff_year` the calendar year of its last payment when
 *  it's projected; `payment_counted` whether its payment is in the spending figure the plan starts from (not left
 *  out as a transfer), so it comes off spending once the loan is paid off or sold. */
export interface PlanLoan {
  rate: number | null; payment: number | null; source: "plaid" | "manual" | "inferred" | null;
  note: "no_rate" | "no_payment" | "payment_below_interest" | null;
  account_id?: string; payoff_year?: number | null; payment_counted?: boolean;
}
/** A home, vehicle or company equity from Net worth that can be sold into the plan. `owed` is what's owed on it
 *  today (a loan paid down since its last balance, as on Net worth). `owed_by_year` and `value_by_year` are indexed
 *  by years from today (0 is today) and stop once they stop changing: the last entry holds from then on. */
export interface PlanAsset {
  key: string; name: string; kind: string; value: number; yearly_change: number; owed: number;
  owed_by_year?: number[]; value_by_year?: number[]; loan?: PlanLoan | null;
}
export interface PlanData {
  plan: RetirementPlan;
  is_default: boolean;
  current: number;
  computed: {
    annual_spending: number; yearly_savings: number; expected_return: number;
    /** Yearly savings is what went into your investments in the last 12 months (false: a figure typed on the old card). */
    savings_measured?: boolean;
    /** The day the investment history starts, when that's less than a year ago. */
    savings_since?: string | null;
  };
  assets: PlanAsset[];
  year: number;
}

interface XrayRule { name: string; ok: boolean; info?: boolean; detail: string }

/** GET /api/investments?period=… */
export interface Investments {
  today: string;
  total: number;
  unrealized_gain: number | null;
  cost_basis: number | null;
  day_change: number | null;
  day_change_pct: number | null;
  cost_missing: number;
  cost_missing_value: number;
  holdings: Holding[];
  allocation: Record<AllocKey, AllocItem[]>;
  income: { months: string[]; income: number[]; fees: number[]; income_12m: number; fees_12m: number };
  history: {
    dates: string[];
    value: number[];
    flows: number[];
    invested: number[];
    twr: number[];
    benchmark: (number | null)[];
    missing_prices: string[];
    estimated_before: string | null;
  };
  performance: Performance;
  periods: Record<string, Performance>;
  xray: XrayRule[];
  plan: PlanData;
  accounts: InvAccount[];
  activity: Activity[];
}

export interface Quote { price: number; prev_close: number | null; time: number | null; type: string | null }
/** GET /api/investments/live, and each event of /api/investments/stream. */
export interface LiveQuotes { quotes: Record<string, Quote>; market: "open" | "closed" | string; as_of: string }

/** GET /api/tracked/{id}: the funds you entered for a balance-only account. */
export interface Tracked {
  positions: { security_id: string; ticker: string | null; name: string | null; shares: number | null; pct: number | null; last_value: number | null }[];
  contributions: { date: string; amount: number }[];
}

/** A line chart's series. `cls` picks its color, as in the classic app. */
export interface Series {
  name: string;
  values: (number | null)[];
  cls: "s-main" | "s-alt" | "s-muted";
  area?: boolean;
  step?: boolean;
}
