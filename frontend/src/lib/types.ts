// The shapes of Runway's API replies that the pages use (see runway/server.py).

interface User { name?: string; email?: string; local?: boolean }
export interface Brand {
  institution?: string | null;
  /** The account's logo: the one you chose, else its institution's (Logo.dev's, once Runway has fetched it); null for its letter. */
  src?: string | null;
  /** The institution's logo, which a choice replaces. */
  auto?: string | null;
  initial?: string;
}
/** The last sync's log line; `at` is when it ran, ISO with its UTC offset. */
interface SyncLog { ok: boolean; message?: string; at?: string }

/** GET /api/state (runway/server.py api_state). */
export interface SentryConfig {
  dsn: string; environment: string; release: string;
  /** Who's signed in, as a code that doesn't say who, so reports count the people they affect. */
  user_id?: string | null;
}

export interface AppState {
  connected: boolean;
  brands?: Record<string, Brand>;
  /** Each bank connection's logo by its institution's name (null: its letter). */
  connection_logos?: Record<string, string | null>;
  simplefin?: boolean;
  has_api_key?: boolean;
  llm_model?: string;
  card_ai_model?: string;
  /** When the banks last synced without an error, ISO with its UTC offset. */
  last_sync_ok?: string | null;
  last_log?: SyncLog | null;
  /** What banks said on that sync (an expired login, say): it still worked, but they need you. */
  sync_warnings?: string[];
  last_llm_error?: string | null;
  review_count?: number;
  plaid_undecided?: number;
  horizon_days?: number;
  syncing?: boolean;
  primary_account?: string | null;
  auto_ai_on_sync?: boolean;
  churn_ai_web?: boolean;   // Churning's "Fill in the rest with AI" searches the web
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
  kind: "card" | "recurring" | "fee" | string;
  key?: string;
  category?: string | null;
  estimated?: boolean;
  overridden?: boolean;
  original_amount?: number;
  /** The forecast account's balance right after it (not on a fee: that's a charge on a card). */
  balance_after?: number;
  account_id?: string | null;
  account?: string | null;
  /** Its merchant's logo (a recurring item's: from its last matched transaction). */
  logo?: string | null;
  /** A card statement's card. */
  card_id?: string;
  /** A recurring item's id (in Recurring). */
  recurring_id?: number;
  /** A recurring payment that's partly come (in parts): what has, signed like amount; amount is the rest. */
  paid_so_far?: number;
  /** An annual fee (kind "fee", Overview.fees): the day the card statement it's on is paid, and the account that pays it
   *  (null when that payment isn't in the forecast). */
  paid_on?: string | null;
  paid_from?: string | null;
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
  /** How the forecast pays the statement (Settings → Accounts): what it pays on the due date out of what's left
   *  (remaining), and what that leaves to carry into the next statement (below zero when a payment you edited is more
   *  than what's left: the extra comes off the next one). */
  pay_mode?: "full" | "minimum" | "fixed";
  payment?: number;
  carried?: number;
  /** More refunds (or overpayment) than charges since the close: a credit that comes off the next statement. */
  credit?: number;
  /** The APR the forecast charges interest at, and whose it is: the one you entered, or the issuer's (through Plaid). */
  apr?: number | null;
  apr_source?: "you" | "issuer" | null;
  due_date: string;
  avg_monthly_spend?: number | null;
  avg_cycles?: number;
  /** Where the statement is from: Plaid, or entered by you. */
  statement_source?: "plaid" | "manual";
  /** One you entered that a newer one should have replaced by now. */
  statement_stale?: boolean;
}

export interface Missed { key: string; name: string; amount: number; date: string; account_id?: string; account_name?: string }

export interface Overview {
  today: string;
  dates: string[];
  total: number[];
  low: { date: string; balance: number };
  /** balance: the bank's posted balance plus what's pending on the account (pending, money out negative). */
  accounts: {
    id: string; name: string; kind: string; balance: number; pending?: number; balance_date?: string | null;
  }[];
  events: ForecastEvent[];
  /** Churning cards' annual fees: charges on cards, listed with what's coming up but not in events or the balances
   *  (each is in its card's statement payment). */
  fees?: ForecastEvent[];
  cards: CardSummary[];
  unlinked_cards?: CardSummary[];
  warnings: string[];
  /** The same warnings, each with the page where it's put right. */
  /** `setting`: changing a setting on that page puts it right (false: an overdue payment, a statement still to come). */
  warning_links: { text: string; href: string; setting?: boolean }[];
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

interface BudgetChange {
  date: string; account_id: string; kind: "budget" | "card"; name: string; amount: number; category?: string; account?: string; charged?: number;
  /** A card with no statement yet: its cycle is assumed (closes at the month's end, paid 25 days later). */
  assumed_cycle?: boolean;
}
