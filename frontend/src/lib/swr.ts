// The last reply of a list, kept in memory so the next visit paints it at once while the fresh one loads (stale while
// revalidate): the Transactions list, and what that page and Recurring need beside it. Nothing here is written to disk.
//
// What goes in is only what doesn't depend on today, the forecast or a balance: callers drop those fields before
// `remember` (accounts without their balances, recurring items without their due dates). Whoever paints from here marks
// it as not yet current, and a refresh that fails takes it down again rather than leave old rows looking current.
//
// It is emptied whenever what it holds may no longer be this person's to show, or may be wrong:
//   - locking (lib/lock.svelte.ts lockNow, which the server's 423 also ends in) and forgetting the lock (signing out);
//   - a 401 (the sign-in is gone);
//   - a change made through the API (lib/api.ts): a save, a sync, anything that isn't a read or a check-in.
// A read that was still in flight when it was emptied must not fill it again: `epoch` says which life a read began in.

const store = new Map<string, unknown>();
let epoch = 0;

/** Note this when a read starts and pass it to `remember` with the reply. */
export const cacheEpoch = (): number => epoch;

/** Keep a copy of `value` (raw API data, as it came) under `key`, unless the cache was emptied since `at`. */
export function remember<T>(key: string, value: T, at: number = epoch): void {
  if (at === epoch) store.set(key, structuredClone(value));
}

/** A copy of what was last kept under `key`, or undefined: yours to change without touching the cache. */
export function recall<T>(key: string): T | undefined {
  return store.has(key) ? structuredClone(store.get(key)) as T : undefined;
}

/** Forget everything. */
export function clearCache(): void {
  store.clear();
  epoch++;
}

if (typeof window !== "undefined") {
  // A 401: the sign-in is gone on the server, and so is the right to show what it let you read.
  window.addEventListener("runway:signed-out", () => clearCache());
}
