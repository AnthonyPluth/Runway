// The shapes of Runway's API replies that the pages use (see runway/server.py).

export interface User { name?: string; email?: string; local?: boolean }
export interface Brand { institution?: string; logo?: string; initial?: string }
export interface SyncLog { ok: boolean; message?: string }

export interface AppState {
  connected: boolean;
  horizon_days?: number;
  review_count?: number;
  syncing?: boolean;
  last_log?: SyncLog | null;
  last_sync_ok?: string | null;
  version?: string;
  user?: User | null;
  brands?: Record<string, Brand>;
  primary_account?: string;
}

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
  accounts: { id: string; name: string; kind: string; balance: number }[];
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
  } | null;
}
