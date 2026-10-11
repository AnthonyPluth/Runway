// The device cache's IndexedDB (lib/deviceCache.ts): one database with one object store of rows keyed by `k`. Every
// call here can fail (a private window, a full disk, storage the browser evicted or blocked): it rejects, and the
// caller treats that as no persistent cache, never as an error to show.
const DB = "runway-cache";
const STORE = "rows";
const VERSION = 1;

export interface Row { k: string; [field: string]: unknown }

/** Whether this browser offers IndexedDB at all (reading `indexedDB` can itself throw where storage is blocked). */
export function available(): boolean {
  try { return typeof indexedDB !== "undefined" && !!indexedDB; } catch { return false; }
}

let opening: Promise<IDBDatabase> | null = null;
let openedWith: IDBFactory | null = null;   // (a test's fresh fake IndexedDB is a new one)
function db(): Promise<IDBDatabase> {
  if (opening && openedWith === indexedDB) return opening;
  openedWith = indexedDB;
  const p: Promise<IDBDatabase> = new Promise<IDBDatabase>((ok, no) => {
    const r = indexedDB.open(DB, VERSION);
    r.onupgradeneeded = () => { if (!r.result.objectStoreNames.contains(STORE)) r.result.createObjectStore(STORE, { keyPath: "k" }); };
    r.onsuccess = () => {
      const d = r.result;
      // Another tab deleting the database (signing out there) asks this one to let go: it does, and opens afresh later.
      d.onversionchange = () => { d.close(); if (opening === p) opening = null; };
      d.onclose = () => { if (opening === p) opening = null; };   // (the browser closed it: storage cleared)
      ok(d);
    };
    r.onerror = () => no(r.error ?? new Error("IndexedDB failed to open"));
    r.onblocked = () => no(new Error("IndexedDB is blocked"));
  });
  opening = p;
  p.catch(() => { if (opening === p) opening = null; });
  return p;
}

function run<T>(mode: IDBTransactionMode, work: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  return db().then((d) => new Promise<T | undefined>((ok, no) => {
    const tx = d.transaction(STORE, mode);
    let out: T | undefined;
    const r = work(tx.objectStore(STORE));
    if (r) r.onsuccess = () => { out = r.result; };
    tx.oncomplete = () => ok(out);
    tx.onerror = () => no(tx.error ?? new Error("IndexedDB failed"));
    tx.onabort = () => no(tx.error ?? new Error("IndexedDB aborted"));
  }));
}

export const getRow = (k: string): Promise<Row | undefined> => run<Row>("readonly", (s) => s.get(k) as IDBRequest<Row>);
export const allRows = async (): Promise<Row[]> => (await run<Row[]>("readonly", (s) => s.getAll() as IDBRequest<Row[]>)) ?? [];
export const putRow = async (row: Row): Promise<void> => { await run("readwrite", (s) => s.put(row)); };
export const removeRows = async (keys: string[]): Promise<void> => {
  if (keys.length) await run("readwrite", (s) => { for (const k of keys) s.delete(k); });
};

/** Delete the whole database: nothing of it is left on the device. Writes already queued finish first (IndexedDB
 *  runs them in order), so they're deleted too. */
export async function destroy(): Promise<void> {
  const was = opening;
  opening = null;
  if (was) await was.then((d) => d.close(), () => { /* it never opened: nothing to close */ });
  await new Promise<void>((ok, no) => {
    const r = indexedDB.deleteDatabase(DB);
    r.onsuccess = () => ok();
    r.onerror = () => no(r.error ?? new Error("IndexedDB failed to delete"));
    // (blocked: another tab holds it open; it closes on onversionchange above, and the delete then goes ahead)
  });
}
