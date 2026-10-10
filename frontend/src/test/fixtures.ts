import type { Category } from "$lib/types";
import type { OrderSummary, Split, Tx } from "$lib/components/transactions/types";
import type { Holding } from "$lib/components/investments/types";

export const category = (name: string, extra: Partial<Category> = {}): Category => ({ name, path: [name], depth: 0, top: name, ...extra });

export const tx = (extra: Partial<Tx> = {}): Tx => ({
  id: "t1", account_id: "a1", account_name: "Checking", posted: "2026-03-10", amount: -12.5,
  payee: "Blue Bottle", description: "BLUE BOTTLE #123", category: "Coffee", category_source: null, confidence: null,
  needs_review: 0, pending: 0, recurring_id: null, recurring_name: null, recurring_linked_by: null,
  is_split: 0, splits: [], notes: null, bank_posted: null, bank_amount: null, retail: null, logo: null,
  logo_account: null, brand: null, source: "plaid", ...extra,
});

/** A part of a split transaction (Tx.splits). */
export const txSplit = (category: string, amount: number, extra: Partial<Split> = {}): Split => ({ id: 1, category, amount, note: null, ...extra });
/** The store order a charge paid for (Tx.retail). */
export const txOrder = (extra: Partial<OrderSummary> = {}): OrderSummary => ({
  order_id: "o1", charge_id: "c1", retailer: "amazon", order_number: "111-1", channel: null, items: 1, ...extra,
});

export const holding = (extra: Partial<Holding> = {}): Holding => ({
  security_id: "s1", group: "", ticker: "VTI", name: "Vanguard Total Market", type: "etf", asset_class: "US stocks", sector: null,
  is_cash: false, quantity: 10, value: 2500, cost_basis: 2000, cost_known: true, cost_manual: false, accounts: ["Brokerage"],
  price: 250, lots: [], allocation: 0.5, gain: 500, gain_pct: 0.25, day_change: 12, day_change_pct: 0.005, ...extra,
});
