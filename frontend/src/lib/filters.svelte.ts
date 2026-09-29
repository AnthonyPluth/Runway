// The Transactions list's filters. They live here, not in the page, so they survive moving between pages and so
// other pages can open Transactions already filtered: set them with showTransactions() and go to #transactions.
export interface TxFilters { q: string; account: string; category: string; month: string; scope: string }

const blank = (): TxFilters => ({ q: "", account: "", category: "", month: "", scope: "" });
export const txFilters = $state({ transactions: blank(), review: blank() });

/** Open Transactions filtered (e.g. a budget line's spending that month). Unset filters are cleared. */
export function showTransactions(f: Partial<TxFilters>): void {
  Object.assign(txFilters.transactions, blank(), f);
  location.hash = "#transactions";
}
