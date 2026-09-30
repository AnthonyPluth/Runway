// The shapes of Runway's API replies that the pages use (see runway/server.py).

export interface User { name?: string; email?: string; local?: boolean }
export interface Brand { institution?: string; /** Logo.dev's logo for the institution, once Runway has fetched it. */ src?: string | null; initial?: string }
export interface SyncLog { ok: boolean; message?: string }

/** GET /api/state (runway/server.py api_state). */
export interface SentryConfig { dsn: string; environment: string; release: string }

export interface AppState {
  connected: boolean;
  brands?: Record<string, Brand>;
  simplefin?: boolean;
  has_api_key?: boolean;
  llm_model?: string;
  last_sync_ok?: string | null;
  last_log?: SyncLog | null;
  last_llm_error?: string | null;
  review_count?: number;
  plaid_undecided?: number;
  horizon_days?: number;
  syncing?: boolean;
  primary_account?: string | null;
  auto_ai_on_sync?: boolean;
  realie_configured?: boolean;
  finnhub_configured?: boolean;
  logodev_configured?: boolean;
  database?: "sqlite" | "postgres";
  version?: string;
  /** Where the web app sends its error reports, when Runway is set up for them. */
  sentry?: SentryConfig | null;
  owners?: string[];
  user?: User | null;
  /** The getting-started checklist: which steps are done, and whether it's been put away. */
  setup?: { bank: boolean; primary: boolean; recurring: boolean; budgets: boolean; dismissed: boolean };
}

/** GET /api/categories, in tree order: each category followed by its subcategories. */
export interface Category {
  name: string;
  parent?: string | null;
  is_transfer?: boolean | number;
  is_income?: boolean | number;
  path: string[];
  depth: number;
  top: string;
  /** Its emoji and #rrggbb color: what you picked, or Runway's default for the name. */
  icon?: string;
  color?: string;
  custom_icon?: string | null;
  custom_color?: string | null;
  transactions?: number;
  [key: string]: unknown;
}

/** GET /api/accounts (a row of the accounts table, plus its Plaid link). */
export interface Account {
  id: string;
  name: string;
  display_name?: string | null;
  kind: string;
  balance?: number | null;
  hidden?: boolean | number;
  owner?: string | null;
  [key: string]: unknown;
}
export const accountName = (a: Account) => a.display_name || a.name;

export interface ForecastEvent {
  date: string;
  name: string;
  amount: number;
  kind: "card" | "recurring" | string;
  key?: string;
  category?: string | null;
  estimated?: boolean;
  overridden?: boolean;
  original_amount?: number;
  balance_after: number;
  account_id?: string;
  account?: string;
  /** Its merchant's logo (a recurring item's: from its last matched transaction). */
  logo?: string | null;
  /** A card statement's card. */
  card_id?: string;
}

export interface CardSummary {
  id: string;
  name: string;
  owed_now: number;
  statement_key: string;
  statement_balance: number;
  statement_set?: boolean;
  statement_reported?: number;
  last_close: string;
  remaining: number;
  minimum_payment?: number | null;
  due_date: string;
  avg_monthly_spend?: number | null;
  avg_cycles?: number;
}

export interface Missed { key: string; name: string; amount: number; date: string; account_id?: string; account_name?: string }

export interface Overview {
  today: string;
  dates: string[];
  total: number[];
  low: { date: string; balance: number };
  /** daily_spend: the everyday spending the forecast takes out of the account each day (when that's turned on). */
  accounts: { id: string; name: string; kind: string; balance: number; daily_spend?: number }[];
  events: ForecastEvent[];
  cards: CardSummary[];
  unlinked_cards?: CardSummary[];
  warnings: string[];
  missed?: Missed[];
  budget?: {
    monthly: number;
    total: number[];
    low: { date: string; balance: number };
    skipped: { category: string; reason: string }[];
    /** What sticking to the budget takes out, day by day: each budget paid from a forecast account, and each card's
     *  statement made of budgeted spending (charged: what's already on the card, in the first one). */
    changes?: BudgetChange[];
  } | null;
}

export interface BudgetChange {
  date: string; account_id: string; kind: "budget" | "card"; name: string; amount: number; category?: string; account?: string; charged?: number;
}
