// Builders for the API shapes component tests render, so each test states only what it cares about.
import type { Category } from "$lib/types";
import type { Tx } from "$lib/components/transactions/types";
import type { Holding } from "$lib/components/investments/types";

export const category = (name: string, extra: Partial<Category> = {}): Category => ({ name, path: [name], depth: 0, top: name, ...extra });

export const tx = (extra: Partial<Tx> = {}): Tx => ({
  id: "t1", account_id: "a1", account_name: "Checking", posted: "2026-03-10", amount: -12.5,
  payee: "Blue Bottle", description: "BLUE BOTTLE #123", category: "Coffee", ...extra,
});

export const holding = (extra: Partial<Holding> = {}): Holding => ({
  security_id: "s1", group: "", ticker: "VTI", name: "Vanguard Total Market", type: "etf", asset_class: "US stocks", sector: null,
  is_cash: false, quantity: 10, value: 2500, cost_basis: 2000, cost_known: true, cost_manual: false, accounts: ["Brokerage"],
  price: 250, lots: [], allocation: 0.5, gain: 500, gain_pct: 0.25, day_change: 12, day_change_pct: 0.005, ...extra,
});
