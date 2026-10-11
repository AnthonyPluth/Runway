// The last reply of a list, kept in memory so the next visit paints it at once while the fresh one loads (stale while
// revalidate): the Transactions list, and what that page and Recurring need beside it. Nothing here is written to disk;
// with the app lock on and a passkey that gives a PRF secret, lib/deviceCache.ts keeps an encrypted copy of what's
// remembered here on the device (`persistWith`), and what it kept is read back when memory has nothing.
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

/** Where what's remembered also goes (lib/deviceCache.ts), and where a miss here looks next. None of these may throw. */
export interface Persisted {
  /** Keep this too: what `remember` kept. */
  write(key: string, value: unknown): void;
  /** What was kept under `key` on an earlier visit, as a fresh copy; undefined when there's none to show. */
  read(key: string): unknown;
  /** A change went through (`dataChanged`): what was kept may no longer be right. */
  changed(): void;
}
let persisted: Persisted | null = null;
/** Where remembered replies also go (lib/deviceCache.ts registers itself). */
export function persistWith(p: Persisted | null): void { persisted = p; }

/** Note this when a read starts and pass it to `remember` with the reply. */
export const cacheEpoch = (): number => epoch;

/** Keep a copy of `value` (raw API data, as it came) under `key`, unless the cache was emptied since `at`. */
export function remember<T>(key: string, value: T, at: number = epoch): void {
  if (at !== epoch) return;
  // A copy that can't be made (not plain data) is a reply that isn't kept: remembering must never fail the load it follows.
  try { store.set(key, structuredClone(value)); } catch { store.delete(key); return; }
  try { persisted?.write(key, value); } catch { /* not kept on the device: it's still in memory */ }
}

/** A copy of what was last kept under `key`, or undefined: yours to change without touching the cache. */
export function recall<T>(key: string): T | undefined {
  if (store.has(key)) return structuredClone(store.get(key)) as T;
  try { return persisted?.read(key) as T | undefined; } catch { return undefined; }
}

/** Forget everything kept in memory (locking, signing out; the device's copy is lib/deviceCache.ts's to close or wipe). */
export function clearCache(): void {
  store.clear();
  epoch++;
}

/** A change went through the API (lib/api.ts): forget everything, here and the device's copy. */
export function dataChanged(): void {
  clearCache();
  try { persisted?.changed(); } catch { /* the device's copy is then wiped when it can't be read */ }
}

if (typeof window !== "undefined") {
  // A 401: the sign-in is gone on the server, and so is the right to show what it let you read.
  window.addEventListener("runway:signed-out", () => clearCache());
}
