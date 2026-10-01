// The Transactions list's filters. They live here, not in the page, so they survive moving between pages and so
// other pages can open Transactions already filtered: set them with showTransactions() and go to #transactions.
export interface TxFilters { q: string; account: string; category: string; month: string; scope: string }

const blank = (): TxFilters => ({ q: "", account: "", category: "", month: "", scope: "" });
export const txFilters = $state({ transactions: blank(), review: blank() });

// Whether All lists what's categorized Ignore (hidden unless you ask). Not a filter: Clear filters leaves it alone.
export const txShow = $state({ ignored: false });

/** Open Transactions filtered (e.g. a budget line's spending that month). Unset filters are cleared. */
export function showTransactions(f: Partial<TxFilters>): void {
  Object.assign(txFilters.transactions, blank(), f);
  location.hash = "#transactions";
}

/** Whether any filter is narrowing the list (the scope only qualifies a month). */
export const isFiltered = (f: TxFilters): boolean => !!(f.q || f.account || f.category || f.month);

/** Back to every transaction: search, account, category and month all cleared. */
export function clearAll(f: TxFilters): void {
  Object.assign(f, blank());
}
