// The shapes of the Investments page's API replies (runway/server.py, runway/portfolio.py).

/** One account's share of a holding (the same fund in two accounts is one holding with two lots). */
export interface Lot {
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

export interface Performance {
  period?: string;
  start?: string;
  start_value?: number;
  end_value?: number;
  net_deposits?: number;
  gain?: number;
  return?: number;
  benchmark_return?: number | null;
}

export interface AllocItem { name: string; value: number; share: number }
export type AllocKey = "asset_class" | "account" | "sector" | "holding";

export interface FireFigures {
  annual_spending: number;
  yearly_savings: number;
  expected_return: number;
  withdrawal_rate: number;
}
export interface Fire extends FireFigures {
  current: number;
  computed: FireFigures;
  saved: string[];
}

export interface XrayRule { name: string; ok: boolean; info?: boolean; detail: string }

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
  fire: Fire;
  accounts: InvAccount[];
  activity: Activity[];
}

/** What SimpleFIN sends for an investment account (Settings' diagnostics). */
export interface SimplefinSeen { id?: string; org?: string; name: string; positions: number; fields: string[] }

/** GET /api/plaid/status (the parts this page uses). */
export interface PlaidStatus {
  items: { item_id: string; institution_name: string | null; error: string | null }[];
  last_inv_sync: string | null;
  simplefin_last_sync: string | null;
  simplefin_seen: SimplefinSeen[];
  inv_accounts: number;
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
