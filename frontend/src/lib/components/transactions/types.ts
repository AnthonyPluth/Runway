// The shapes Transactions and Review use (see runway/server.py api_transactions, api_ai_*, api_recurring).
import type { ForecastEvent } from "$lib/types";
import type { OrderSummary } from "$lib/components/orders/retail";

export interface Split { category: string; amount: number; note?: string | null }

/** A row of GET /api/transactions. */
export interface Tx {
  id: string;
  account_id: string;
  account_name?: string | null;
  posted: string;
  amount: number;
  payee?: string | null;
  description?: string | null;
  category?: string | null;
  category_source?: string | null;
  confidence?: number | null;
  needs_review?: number | boolean;
  pending?: number | boolean;
  recurring_id?: number | null;
  recurring_name?: string | null;
  is_split?: number | boolean;
  splits?: Split[];
  retail?: (OrderSummary & { order_id: string }) | null;
  logo?: string | null;
}
export interface TxList { items: Tx[]; total: number }

/** POST /api/transactions/{id}/category (and /api/ai/apply) may offer to remember the category for the merchant. */
export interface RuleOffer { merchant: string; match?: string; replaces?: string | null }

/** An item of GET /api/recurring (only what the ↻ picker needs). */
export interface RecurringItem { id: number; name: string; frequency: string; account_id: string }

/** A projected event, as Upcoming shows it. */
export type UpcomingEvent = ForecastEvent & { late_from?: string | null };

/** A merchant group of POST /api/ai/suggest. */
export interface AiGroup {
  merchant: string;
  direction: "in" | "out";
  count: number;
  total: number;
  examples: string[];
  tx_ids: string[];
  category: string | null;
  new_category: { name: string; parent?: string | null } | null;
  confidence: number;
}

/** A row of GET /api/ai/log. */
export interface AiLogRow {
  id: number;
  at: string;
  purpose: "review" | "orders" | string;
  model?: string | null;
  merchants: number;
  answered: number;
  new_cats?: number;
  ok: number | boolean;
  seconds?: number | null;
  message?: string | null;
  reply?: string | null;
}
