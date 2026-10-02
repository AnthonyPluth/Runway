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
  /** Under a category filter, a split one's parts in that category (or its subcategories): what they add up to, and
   *  their categories. The list shows that much of it, and a category picked for it changes only those parts. */
  match?: { amount: number; categories: string[] } | null;
  retail?: (OrderSummary & { order_id: string }) | null;
  logo?: string | null;
  /** A big merchant's: the brand's name a sync gave it ("Amazon") and the one the bank's text gives ("Amzn Mktp Us"),
   *  and which of them it has. */
  brand?: BrandChoice | null;
}
export interface BrandChoice { brand: string; bank_name: string; using: "brand" | "bank" }
/** `family`: under a category filter, the category and its subcategories (a receipt shows just their items). */
export interface TxList { items: Tx[]; total: number; family?: string[] }

/** POST /api/transactions/{id}/category (and /api/ai/apply) may offer to remember the category for the merchant. */
export interface RuleOffer { merchant: string; match?: string; replaces?: string | null }

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
