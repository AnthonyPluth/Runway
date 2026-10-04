// The shapes Transactions and Review use (see runway/server/api/transactions.py, recurring.py).
import type { ForecastEvent } from "$lib/types";

// GET /api/transactions's rows (Tx) and the list (TxList), from the API contract (lib/api-types.ts, generated from
// runway/server/contract.py). A row's `match`: under a category filter, a split one's parts in that category (or its
// subcategories), which the list shows and a category picked for it changes. `logo_account`: a card payment's account
// whose institution's logo (or letter) it shows when it has no logo of its own. `brand`: a big merchant's name a sync
// gave it ("Amazon") and the one the bank's text gives ("Amzn Mktp Us"), and which it has. The list's `family`: under a
// category filter, the category and its subcategories; `sum`: what they all add up to, as the day totals count them.
export type { BrandChoice, OrderSummary, Split, Tx, TxList } from "$lib/api-types";

/** POST /api/transactions/{id}/category (and /api/ai/apply, and /api/transactions/bulk for one merchant's) may offer to
 *  remember the category for the merchant: the text a rule would match, the category a rule already gives it, and how
 *  many more transactions the rule would categorize now. */
export interface RuleOffer { merchant: string; match?: string; replaces?: string | null; also_updated?: number }

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
