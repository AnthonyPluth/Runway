// The encrypted copy on the device of what lib/swr.ts remembers (#357, #379), so that after a cold start (opening the
// app again, a reload) the lists it covers paint at once from the device while the server is asked, as they already
// do within a visit. Read-only and never the last word: whatever paints from here is marked as not yet current, and
// the server is always asked again (lib/api.ts's ETags make that a 304 when nothing changed).
//
// Only with the app lock on (lib/lock.svelte.ts) and a passkey that gives a PRF secret: without either, nothing is
// kept and the app works exactly as before. Opening it needs both halves of its key (lib/cacheCrypto.ts): the
// passkey's PRF output (only from an unlock, with Face ID, Touch ID or the passcode) and a share the server holds for
// this device's lock (POST /api/lock/key-share, runway/applock.py), handed out once per unlock. The key lives in
// memory only, as a non-extractable CryptoKey, and goes when it locks (and with the re-lock timers), so a locked app
// can't read its own cache. In IndexedDB (lib/idb.ts) there are only sealed rows:
//   - "share": the device's own copy of the server's share, sealed under the PRF output alone, with when it was
//     fetched and until when it may be used (`expiresAt`: the server's `expires`, but never more than SHARE_WINDOW_MS
//     after it was fetched by this device's clock, whichever is sooner, so a wrong clock can't stretch it);
//   - "r:<name>": a record, sealed under the cache key, tagged with the data version (dataVersionOf: what /api/state
//     says) it was fetched under and the schema it was written with.
//
// What goes in is only what lib/swr.ts remembers (its callers drop balances, statements, due dates and expected
// amounts first), only under the names in PERSISTED, and never anything carrying one of EXCLUDED's fields: a reply
// that would is simply not kept. Never forecast- or balance-derived numbers.
//
// The whole store is wiped (wipe) on signing out, a 401, a 423, the lock being turned off or found off, a share the
// server refuses (400, 403), a share that changed (a new one can't open the old records), and a device copy of the
// share past its time that can't be fetched again. A record that doesn't open (tampered, another key, an old schema,
// another data version) is a miss and is deleted. IndexedDB failing (a private window, a full disk, evicted storage)
// only means nothing is kept: the in-memory cache goes on as before. Nothing here throws, shows an error, or holds up
// the app or the lock.
import { apiCall } from "./contract";
import * as idb from "./idb";
import { persistWith } from "./swr";
import { b64uDecode } from "./webauthn";
import { PRF_INPUT, SECRET_BYTES, aad, cacheKey, openBytes, openText, seal, wrappingKey } from "./cacheCrypto";

/** The schema of what's stored: a row written with another one is a miss (and deleted). */
export const SCHEMA = 1;
/** How long the device may go on using its own copy of the share (runway/applock.py SHARE_WINDOW). */
export const SHARE_WINDOW_MS = 72 * 3600 * 1000;
/** How far the clock may have gone back since the share was fetched before its copy is no longer trusted. */
const SKEW_MS = 5 * 60 * 1000;
/** At most this many records (the oldest go first). */
export const MAX_RECORDS = 24;
/** The names lib/swr.ts remembers that may be kept on the device. */
export const persistable = (name: string): boolean => ["accounts", "categories", "recurring"].includes(name) || name.startsWith("transactions?");
/** Fields that are worked out from today, the forecast or a balance: a reply carrying one (not empty) isn't kept. */
export const EXCLUDED = new Set(["balance", "available", "balance_date", "statement", "statements", "next_date", "late_date",
  "missed", "expected_amount", "suggested_amount", "skipped", "carried", "expected"]);

let key: CryptoKey | null = null;     // the cache key, while unlocked
let gen = 0;                          // bumped by closing and wiping: work started before then stops
let changes = 0;                      // bumped when the records are dropped after a change: writes started before then stop
let version: string | null = null;    // the data version /api/state last said
let broken = false;                   // IndexedDB failed during this unlock: nothing more is written until the next
const kept = new Map<string, { version: string; json: string }>();   // the records opened at unlock
const pending = new Set<Promise<unknown>>();                          // work under way (for `settled`)
function track<T>(p: Promise<T>): Promise<T> {
  pending.add(p);
  void p.finally(() => pending.delete(p)).catch(() => { /* (track's callers never reject) */ });
  return p;
}
/** Resolves once the work under way (opening, writing, deleting) is done: for tests, and for whoever must wait. */
export async function settled(): Promise<void> {
  while (pending.size) await Promise.allSettled([...pending]);
}
/** Whether the cache key is in memory now (unlocked, with a cache). */
export const isOpen = (): boolean => key !== null;

/** Whether this browser can keep the cache at all: IndexedDB and WebCrypto (only in secure contexts). */
export function supported(): boolean {
  try { return idb.available() && typeof crypto !== "undefined" && !!crypto.subtle; } catch { return false; }
}

/** What the unlock asks the passkey's PRF for, or undefined when there's nowhere to keep a cache (then nothing is asked). */
export const prfInput = (): Uint8Array<ArrayBuffer> | undefined => (supported() ? PRF_INPUT : undefined);

/** The data version a state reply stands for: the release and the last sync. A sync (or an update) changes it, and
 *  what was kept under another one is dropped. */
export function dataVersionOf(s: { version?: string; last_sync_ok?: string | null; last_log?: { at?: string | null } | null } | null): string | null {
  if (!s) return null;
  return JSON.stringify([s.version ?? "", s.last_sync_ok ?? "", s.last_log?.at ?? ""]);
}

/** /api/state answered (lib/app.svelte.ts): records kept under any other data version are dropped. */
export function noteDataVersion(v: string | null): void {
  if (!v || v === version) return;
  version = v;
  const stale = [...kept].filter(([, e]) => e.version !== v).map(([name]) => name);
  for (const name of stale) kept.delete(name);
  if (!key || !stale.length) return;
  void track(idb.removeRows(stale.map((n) => `r:${n}`)).catch(() => { /* they don't open under this version anyway */ }));
}

const shareAad = (device: string, fetchedAt: number, expiresAt: number) => aad("runway.cache.share", SCHEMA, device, fetchedAt, expiresAt);
const recordAad = (name: string, v: string) => aad("runway.cache.record", SCHEMA, name, v);

/** The share as the server sends it (32 bytes, base64url without padding), or null. */
function parseShare(s: unknown): Uint8Array<ArrayBuffer> | null {
  if (typeof s !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(s)) return null;
  try { const b = b64uDecode(s); return b.length === SECRET_BYTES ? b : null; } catch { return null; }
}

const same = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((x, i) => x === b[i]);

/** Whether a kept copy of the share may still be used now, by its own times. */
const inWindow = (fetchedAt: unknown, expiresAt: unknown, now: number) =>
  typeof fetchedAt === "number" && typeof expiresAt === "number" && now >= fetchedAt - SKEW_MS && now < expiresAt;

type Kept = { share: Uint8Array<ArrayBuffer>; fetchedAt: number; expiresAt: number } | null | "unreadable";
/** The device's copy of the share, opened with the PRF output: null when there's none, "unreadable" when it's from
 *  another device's lock, another passkey, another schema, or was changed. */
async function keptShare(wrap: CryptoKey, device: string): Promise<Kept> {
  const row = await idb.getRow("share");
  if (!row) return null;
  if (row.v !== SCHEMA || row.device !== device || typeof row.fetchedAt !== "number" || typeof row.expiresAt !== "number") return "unreadable";
  try {
    const share = await openBytes(wrap, shareAad(device, row.fetchedAt, row.expiresAt), row);
    return share.length === SECRET_BYTES ? { share, fetchedAt: row.fetchedAt, expiresAt: row.expiresAt } : "unreadable";
  } catch { return "unreadable"; }
}

/** After an unlock: make the cache key from the PRF output and the server's share, and open what was kept. Never
 *  throws. `deviceId`: this device's lock (the share belongs to it); `prf`: the passkey's PRF output, null when it
 *  gave none (then there's no cache, and whatever was kept goes). */
export function openCache(secret: { deviceId: string | null; prf: Uint8Array | null } | null): Promise<void> {
  closeCache();
  return track(opening(secret, gen));
}
async function opening(secret: { deviceId: string | null; prf: Uint8Array | null } | null, mine: number): Promise<void> {
  const device = secret?.deviceId, prf = secret?.prf ? new Uint8Array(secret.prf) : null;
  try {
    if (!device || !prf || prf.length !== SECRET_BYTES || !supported()) { await wipe(); return; }
    const wrap = await wrappingKey(prf);
    let fresh: { share: Uint8Array<ArrayBuffer>; expires: number } | null = null, status = 0;
    const fetchedAt = Date.now();
    try {
      // `background`: asking for the share isn't a change (lib/api.ts would otherwise drop what's remembered).
      const r = await apiCall<"POST /api/lock/key-share">("/api/lock/key-share", { method: "POST", background: true });
      const share = parseShare(r?.share);
      if (share && typeof r.expires === "number" && Number.isFinite(r.expires)) fresh = { share, expires: r.expires * 1000 };
    } catch (err) { status = (err as { status?: number }).status ?? 0; }
    if (mine !== gen) return;
    if (!fresh) {
      // The server said no: no sign-in or no lock (400), signed out (401), not after this unlock or access ended
      // (403: the server drops the share then), locked (423). Nothing kept may be opened again: it all goes.
      if ([400, 401, 403, 423].includes(status)) { await wipe(); return; }
      // Unreachable, too many asks (429), or an answer it can't use: no cache this time. What's kept stays for the
      // next unlock, as long as the device's copy of the share is still in its window; past it, it all goes.
      const was = await keptShare(wrap, device);
      if (mine === gen && (!was || was === "unreadable" || !inWindow(was.fetchedAt, was.expiresAt, Date.now()))) await wipe();
      return;
    }
    const was = await keptShare(wrap, device);
    if (mine !== gen) return;
    // Another share (the device's lock was made again, or the server lost its own), another passkey, or a copy that
    // doesn't open: the records were sealed under a key that's gone. Start afresh.
    if (was !== null && (was === "unreadable" || !same(was.share, fresh.share))) {
      await idb.destroy();
      if (mine !== gen) return;
    }
    const expiresAt = Math.min(fresh.expires, fetchedAt + SHARE_WINDOW_MS);   // the server's time, but never longer than the window here
    const sealed = await seal(wrap, shareAad(device, fetchedAt, expiresAt), fresh.share);
    if (mine !== gen) return;
    await idb.putRow({ k: "share", v: SCHEMA, device, fetchedAt, expiresAt, ...sealed });
    const k = await cacheKey(prf, fresh.share);
    fresh.share.fill(0);
    if (mine !== gen) return;
    key = k;
    broken = false;
    await load(mine);
  } catch {
    // IndexedDB or WebCrypto failed: no cache this unlock (the in-memory one goes on as before).
    if (mine === gen) { key = null; kept.clear(); }
  } finally {
    prf?.fill(0);
  }
}

/** Open the records with the cache key, keeping those that open under the current data version; the rest go. */
async function load(mine: number): Promise<void> {
  const k = key!;
  const drop: string[] = [];
  for (const row of await idb.allRows()) {
    if (mine !== gen) return;
    if (row.k === "share") continue;
    const name = row.k.startsWith("r:") ? row.k.slice(2) : null;
    if (!name || row.v !== SCHEMA || typeof row.version !== "string" || (version && row.version !== version) || !persistable(name)) {
      drop.push(row.k); continue;
    }
    try {
      const json = await openText(k, recordAad(name, row.version), row);
      JSON.parse(json);
      if (mine !== gen) return;
      kept.set(name, { version: row.version, json });
    } catch { drop.push(row.k); }   // tampered, moved, or sealed under another key: a miss, and it goes
  }
  if (drop.length && mine === gen) await idb.removeRows(drop);
}

/** Whether `v` carries one of EXCLUDED's fields with something in it, anywhere. */
export function carriesExcluded(v: unknown): boolean {
  if (Array.isArray(v)) return v.some(carriesExcluded);
  if (!v || typeof v !== "object") return false;
  return Object.entries(v).some(([f, x]) => (EXCLUDED.has(f) && x !== null && x !== undefined && !(Array.isArray(x) && !x.length)) || carriesExcluded(x));
}

/** Keep `value` (what lib/swr.ts just remembered under `name`) on the device, sealed. Never throws, never waits. */
function write(name: string, value: unknown): void {
  if (!key || broken || !version || !persistable(name) || carriesExcluded(value)) return;
  let json: string | undefined;
  try { json = JSON.stringify(value); } catch { return; }
  if (json === undefined) return;
  const k = key, v = version, mine = gen, change = changes;
  kept.delete(name);   // memory has the fresh one now
  void track((async () => {
    try {
      const sealed = await seal(k, recordAad(name, v), json);
      if (mine !== gen || change !== changes || v !== version) return;   // locked, wiped, changed or synced meanwhile
      await idb.putRow({ k: `r:${name}`, v: SCHEMA, version: v, at: Date.now(), ...sealed });
      const rows = (await idb.allRows()).filter((r) => r.k !== "share");
      if (rows.length > MAX_RECORDS) {
        rows.sort((a, b) => Number(a.at) - Number(b.at));
        await idb.removeRows(rows.slice(0, rows.length - MAX_RECORDS).map((r) => r.k));
      }
    } catch {
      broken = true;   // (a full disk, evicted storage): nothing more this unlock; the old copy of this one goes
      await idb.removeRows([`r:${name}`]).catch(() => { /* then it doesn't open under the next data version either */ });
    }
  })());
}

/** A record kept on an earlier visit, as a fresh copy, if it was fetched under the current data version. */
function read(name: string): unknown {
  const e = kept.get(name);
  if (!e || !key || !version || e.version !== version) return undefined;
  return JSON.parse(e.json);
}

/** A change went through: the records may no longer be right, so they go (the share stays: it's still this device's). */
function changed(): void {
  changes++;
  kept.clear();
  if (!key) return;
  void track(idb.allRows().then((rows) => idb.removeRows(rows.filter((r) => r.k !== "share").map((r) => r.k)))
    .catch(() => { /* not removed: tagged with the old data version, they don't open after the next sync */ }));
}

/** Locked (or the re-lock timers): the key and what was opened go from memory. What's on the device stays, sealed. */
export function closeCache(): void {
  gen++;
  key = null;
  kept.clear();
}

/** Delete everything kept on the device, and close it. Resolves once it's gone (or couldn't be: never rejects). */
export function wipe(): Promise<void> {
  closeCache();
  if (!idb.available()) return Promise.resolve();
  return track(idb.destroy().catch(() => { /* storage that can't be reached holds nothing this can open again without the key */ }));
}

/** At launch: with the lock off here, nothing may be kept; with it on, a device copy of the share past its window
 *  takes everything with it. */
export function sweep(lockOn: boolean): Promise<void> {
  if (!supported()) return Promise.resolve();
  if (!lockOn) return wipe();
  return track(sweeping(gen));
}
async function sweeping(mine: number): Promise<void> {
  try {
    const row = await idb.getRow("share");
    // (an unlock since has fetched the share again, or found it can't: it's that one's to decide)
    if (mine === gen && (!row || !inWindow(row.fetchedAt, row.expiresAt, Date.now()))) await wipe();
  } catch { /* unreadable storage: nothing in it can be opened */ }
}

persistWith({ write, read, changed });

/** For tests: forget the data version too. */
export function resetForTests(): void { closeCache(); version = null; broken = false; }
