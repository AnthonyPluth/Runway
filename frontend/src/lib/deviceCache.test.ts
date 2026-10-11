// @vitest-environment jsdom
// The encrypted cache on the device (lib/deviceCache.ts), with the real lock, lists and API client over a fake fetch and a
// fake IndexedDB: what's kept and what never is (read back from the stored plaintext), tampering, the schema and the
// data version, the key going with every lock, every way it's wiped, the share's window and clock skew, and the cases
// with no cache at all (no PRF, no IndexedDB, IndexedDB failing), where the app behaves as before.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));
vi.mock("$lib/webauthn", async (real) => ({ ...(await real<typeof import("./webauthn")>()), signChallenge: vi.fn() }));

import { installFakeIndexedDB, removeIndexedDB, type FakeIndexedDB } from "../test/fakeIndexedDB";
import { loadAccounts, staleAccounts } from "./accounts";
import { api } from "./api";
import { PRF_INPUT, aad, cacheKey, openText } from "./cacheCrypto";
import { loadRecurring, staleRecurring } from "./components/recurring/load";
import { EXCLUDED, MAX_RECORDS, SCHEMA, SHARE_WINDOW_MS, carriesExcluded, closeCache, dataVersionOf, isOpen, noteDataVersion,
  openCache, resetForTests, settled, sweep } from "./deviceCache";
import { MAX_UNLOCKED_MS, OPENING_MS, forget, lock, lockNow, turnedOff, unlock, wentAway } from "./lock.svelte";
import { clearCache, recall, remember } from "./swr";
import { b64uEncode, signChallenge } from "./webauthn";

const PRF = new Uint8Array(32).fill(7);
const SHARE = new Uint8Array(32).fill(9);
const DEVICE = "dev_1";
const STATUS = { available: true, on: true, locked: false, idle: 60, credential_id: "Y3JlZA", device_id: DEVICE };
const ANSWER = { credential_id: "Y3JlZA", client_data: "e30", authenticator_data: "AA", signature: "AA" };
const NOW = Date.UTC(2026, 9, 11, 12, 0, 0);
const HOUR = 3600 * 1000;

let idb: FakeIndexedDB;
let fetchMock: ReturnType<typeof vi.fn>;
let routes: Record<string, (body?: unknown) => unknown>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const shareReply = (share = SHARE, serverNow = Date.now()) => ({ share: b64uEncode(share), expires: Math.floor(serverNow / 1000) + 72 * 3600 });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  idb = installFakeIndexedDB();
  resetForTests();
  clearCache();
  lock.phase = "off";
  routes = {
    "POST /api/lock/challenge": () => ({ challenge: "Y2g", rp_id: "runway.example", credential_id: "Y3JlZA" }),
    "POST /api/lock/unlock": () => STATUS,
    "POST /api/lock/engage": () => STATUS,
    "POST /api/lock/key-share": () => shareReply(),
  };
  fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const r = routes[`${init?.method ?? "GET"} ${path.split("?")[0]}`];
    if (!r) return json({ error: `unexpected ${path}` }, 404);
    const out = await r(init?.body);
    return out instanceof Response ? out : json(out);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.mocked(signChallenge).mockReset();
});
afterEach(async () => {
  closeCache();
  await settled();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
});

/** Unlock through the lock screen, the passkey giving `prf`, and wait for the cache to open. */
async function unlockWith(prf: Uint8Array | null = PRF) {
  lock.phase = "locked";
  lock.launch = false;
  vi.mocked(signChallenge).mockResolvedValue({ answer: ANSWER, prf: prf && new Uint8Array(prf) });
  expect(await unlock()).toBe(true);
  await settled();
}
/** A cold start as the cache sees it: memory gone (a reload), then the next unlock. */
async function relaunch(prf: Uint8Array | null = PRF) {
  lockNow(false);
  clearCache();
  await settled();
  await unlockWith(prf);
}
const ACCOUNTS = [{ id: "a1", name: "Everyday checking", kind: "checking", hidden: 0, balance: 1234.5, available: 1000,
  balance_date: "2026-10-09", statement: { due: "2026-10-20" }, statements: [{ balance: 50 }] }];
const RECURRING = [{ id: 7, name: "Rent", amount: -900, frequency: "monthly", active: 1, matched_count: 3, next_date: "2026-11-01",
  late_date: "2026-10-01", expected_amount: -905, suggested_amount: -910, skipped: ["2026-12-01"], missed: [{ key: "m" }] }];
const TXS = { items: [{ id: "t1", posted: "2026-10-01", amount: -12.5, payee: "Corner Bakery", category: "Food" }], total: 1, sum: -12.5 };

/** The records on the "disk", opened with the key the test knows: name → the stored plaintext. */
async function storedPlaintext(share = SHARE): Promise<Record<string, string>> {
  const k = await cacheKey(PRF, share), out: Record<string, string> = {};
  for (const row of idb.rows()) {
    if (!String(row.k).startsWith("r:")) continue;
    const name = String(row.k).slice(2);
    out[name] = await openText(k, aad("runway.cache.record", SCHEMA, name, String(row.version)), row);
  }
  return out;
}
const records = () => idb.rows().filter((r) => String(r.k).startsWith("r:")).map((r) => String(r.k).slice(2)).sort();

describe("keeping and reading back", () => {
  it("paints a list after a cold start from the device, sealed, and only under the data version it was fetched with", async () => {
    noteDataVersion("v1");
    await unlockWith();
    expect(isOpen()).toBe(true);
    remember("transactions?ignored=0", TXS);
    await settled();
    expect(records()).toEqual(["transactions?ignored=0"]);
    const [row] = idb.rows().filter((r) => r.k === "r:transactions?ignored=0");
    expect(Object.keys(row).sort()).toEqual(["at", "ct", "iv", "k", "v", "version"]);   // nothing else in the clear
    expect(Buffer.from(row.ct as ArrayBuffer).toString("latin1")).not.toContain("Corner Bakery");

    await relaunch();
    expect(recall("transactions?ignored=0")).toEqual(TXS);
    // A fresh copy each time, as lib/swr.ts promises.
    const got = recall<typeof TXS>("transactions?ignored=0")!;
    got.items[0].payee = "changed";
    expect(recall<typeof TXS>("transactions?ignored=0")!.items[0].payee).toBe("Corner Bakery");
  });

  it("never stores balances, statements, due dates or expected amounts: checked on the stored plaintext", async () => {
    noteDataVersion("v1");
    await unlockWith();
    routes["GET /api/accounts"] = () => ACCOUNTS;
    routes["GET /api/recurring"] = () => RECURRING;
    await loadAccounts();
    await loadRecurring();
    remember("transactions?ignored=0", TXS);
    await settled();
    const plain = await storedPlaintext();
    expect(Object.keys(plain).sort()).toEqual(["accounts", "recurring", "transactions?ignored=0"]);
    for (const text of Object.values(plain)) expect(carriesExcluded(JSON.parse(text))).toBe(false);
    expect(plain.accounts).not.toMatch(/1234|1000|2026-10-09|2026-10-20/);
    expect(plain.recurring).not.toMatch(/2026-11-01|2026-10-01|905|910|2026-12-01/);
    for (const f of EXCLUDED) {
      for (const text of Object.values(plain)) {
        const v = JSON.stringify(JSON.parse(text), (k, x) => (k === f ? (x === null || (Array.isArray(x) && !x.length) ? undefined : "FOUND") : x));
        expect(v).not.toContain("FOUND");
      }
    }
    await relaunch();
    expect(staleAccounts()![0]).toMatchObject({ id: "a1", name: "Everyday checking", balance: null, available: null });
    expect(staleRecurring()).toEqual([{ id: 7, name: "Rent", amount: -900, frequency: "monthly", active: 1, matched_count: 3 }]);
  });

  it("refuses anything carrying an excluded field, and names it doesn't keep", async () => {
    noteDataVersion("v1");
    await unlockWith();
    remember("accounts", [{ id: "a1", balance: 5 }]);
    remember("recurring", [{ id: 1, nested: { next_date: "2026-11-01" } }]);
    remember("overview", { cash: 100 });
    remember("budget?month=2026-10", { rows: [] });
    remember("categories", [{ name: "Food", balance: null, statements: [] }]);   // empty ones are fine
    await settled();
    expect(records()).toEqual(["categories"]);
    expect(recall("accounts")).toEqual([{ id: "a1", balance: 5 }]);   // memory still has it (lib/swr.ts as before)
  });

  it("asks the passkey for its PRF secret in the same prompt as the unlock", async () => {
    await unlockWith();
    expect(vi.mocked(signChallenge).mock.calls[0][1]).toEqual(PRF_INPUT);
    const calls = fetchMock.mock.calls.map((c) => `${c[1]?.method ?? "GET"} ${c[0]}`);
    expect(calls).toEqual(["POST /api/lock/challenge", "POST /api/lock/unlock", "POST /api/lock/key-share"]);
    // The share is asked for as a background call: not a change that empties what's remembered.
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "POST", headers: { "X-Runway": "1" } });
  });
});

describe("tampering", () => {
  async function twoRecords() {
    noteDataVersion("v1");
    await unlockWith();
    remember("accounts", [{ id: "a1", name: "A" }]);
    remember("categories", [{ name: "Food" }]);
    await settled();
  }
  const row = (k: string) => idb.dbs.get("runway-cache")!.get("rows")!.get(k)!;

  it("doesn't open a record moved under another name, and deletes both", async () => {
    await twoRecords();
    const a = row("r:accounts"), c = row("r:categories");
    [a.iv, c.iv] = [c.iv, a.iv];
    [a.ct, c.ct] = [c.ct, a.ct];
    await relaunch();
    expect(recall("accounts")).toBeUndefined();
    expect(recall("categories")).toBeUndefined();
    expect(records()).toEqual([]);
  });

  it("doesn't open a record with a flipped byte, or a changed tag, and deletes it", async () => {
    await twoRecords();
    const ct = new Uint8Array(row("r:accounts").ct as ArrayBuffer);
    ct[0] ^= 0x80;
    await relaunch();
    expect(recall("accounts")).toBeUndefined();
    expect(recall("categories")).toEqual([{ name: "Food" }]);
    expect(records()).toEqual(["categories"]);
    row("r:categories").version = "v2";   // the version tag is authenticated too
    noteDataVersion("v2");
    await relaunch();
    expect(recall("categories")).toBeUndefined();
    expect(records()).toEqual([]);
  });

  it("takes a record written with another schema as a miss, and deletes it", async () => {
    await twoRecords();
    row("r:accounts").v = SCHEMA + 1;
    await relaunch();
    expect(recall("accounts")).toBeUndefined();
    expect(records()).toEqual(["categories"]);
  });

  it("starts afresh when the device's copy of the share doesn't open (changed, or another passkey)", async () => {
    await twoRecords();
    row("share").expiresAt = (row("share").expiresAt as number) + 10 * HOUR;   // stretching the window breaks the seal
    await relaunch();
    expect(records()).toEqual([]);
    expect(recall("accounts")).toBeUndefined();
    expect(isOpen()).toBe(true);   // with the share the server just gave, it goes on afresh
  });
});

describe("the data version", () => {
  it("drops what was kept under another one, and paints nothing until /api/state has said which it is", async () => {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    await settled();
    resetForTests();   // a launch: the version isn't known until /api/state answers
    clearCache();
    await unlockWith();
    expect(recall("categories")).toBeUndefined();
    noteDataVersion("v1");
    expect(recall("categories")).toEqual([{ name: "Food" }]);
    noteDataVersion("v2");   // a sync
    expect(recall("categories")).toBeUndefined();
    await settled();
    expect(records()).toEqual([]);
  });

  it("is the release and the last sync, from the state reply", () => {
    expect(dataVersionOf(null)).toBeNull();
    const a = dataVersionOf({ version: "1.2.0", last_sync_ok: "2026-10-11T07:00:00-04:00", last_log: { at: "2026-10-11T11:00:00+00:00" } });
    expect(a).not.toEqual(dataVersionOf({ version: "1.2.0", last_sync_ok: "2026-10-11T07:00:00-04:00", last_log: { at: "2026-10-11T12:00:00+00:00" } }));
    expect(a).not.toEqual(dataVersionOf({ version: "1.3.0", last_sync_ok: "2026-10-11T07:00:00-04:00", last_log: { at: "2026-10-11T11:00:00+00:00" } }));
    expect(dataVersionOf({})).toEqual(dataVersionOf({ version: undefined, last_sync_ok: null, last_log: null }));
  });

  it("drops the records after a change goes through, but keeps the share", async () => {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    await settled();
    routes["POST /api/transactions/t1/category"] = () => ({ ok: true });
    await api("/api/transactions/t1/category", { method: "POST", body: { category: "Food" } });
    await settled();
    expect(records()).toEqual([]);
    expect(idb.rows().map((r) => r.k)).toEqual(["share"]);
    expect(recall("categories")).toBeUndefined();
  });
});

describe("the key", () => {
  async function open() {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    await settled();
    expect(isOpen()).toBe(true);
  }
  const unreadable = async () => {
    expect(isOpen()).toBe(false);
    clearCache();
    expect(recall("categories")).toBeUndefined();
    remember("categories", [{ name: "Other" }]);   // nor is anything written without it
    await settled();
  };

  it("goes when it locks, and what's on the device stays sealed for the next unlock", async () => {
    await open();
    lockNow();
    await unreadable();
    expect(records()).toEqual(["categories"]);
    await unlockWith();
    clearCache();
    expect(recall("categories")).toEqual([{ name: "Food" }]);
  });

  it("goes with the re-lock timer after time away", async () => {
    await open();
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    wentAway();
    vi.advanceTimersByTime(60_000);
    expect(lock.phase).toBe("locked");
    await unreadable();
  });

  it("goes twelve hours after an unlock however it's used", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(NOW);
    const done = unlockWith();
    await vi.waitFor(() => expect(lock.phase).toBe("unlocked"));
    await done;
    expect(isOpen()).toBe(true);
    vi.advanceTimersByTime(MAX_UNLOCKED_MS);
    expect(lock.phase).toBe("locked");
    expect(isOpen()).toBe(false);
  });
});

describe("wiping", () => {
  async function kept() {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    await settled();
    expect(idb.rows()).toHaveLength(2);
  }
  const gone = async () => {
    await settled();
    expect(isOpen()).toBe(false);
    expect(idb.dbs.has("runway-cache")).toBe(false);   // nothing of it left on the device
  };

  it("on signing out (forgetting the lock), and the promise says when it's gone", async () => {
    await kept();
    await forget();
    expect(idb.dbs.has("runway-cache")).toBe(false);
    await gone();
  });

  it("on a 401", async () => {
    await kept();
    routes["GET /api/categories"] = () => json({ error: "signed out" }, 401);
    window.addEventListener("runway:signed-out", (e) => e.preventDefault(), { once: true });   // (stay on the page)
    await expect(api("/api/categories", { background: true })).rejects.toMatchObject({ status: 401 });
    await gone();
  });

  it("on a 423", async () => {
    await kept();
    routes["GET /api/categories"] = () => json({ error: "locked" }, 423);
    await expect(api("/api/categories", { keep: true })).rejects.toMatchObject({ status: 423 });
    expect(lock.phase).toBe("locked");
    await gone();
  });

  it("when the lock is turned off", async () => {
    await kept();
    turnedOff();
    await gone();
  });

  it("when the server refuses the share: no lock, not after a fresh unlock, access ended, locked", async () => {
    for (const status of [400, 403, 423]) {
      await kept();
      routes["POST /api/lock/key-share"] = () => json({ error: "no" }, status);
      await relaunch();
      await gone();
      routes["POST /api/lock/key-share"] = () => shareReply();
    }
  });

  it("when the share changed: the old records can't be opened with it", async () => {
    await kept();
    const other = new Uint8Array(32).fill(3);
    routes["POST /api/lock/key-share"] = () => shareReply(other);
    await relaunch();
    expect(isOpen()).toBe(true);
    expect(records()).toEqual([]);
    expect(recall("categories")).toBeUndefined();
    remember("categories", [{ name: "New" }]);
    await settled();
    expect(await storedPlaintext(other)).toEqual({ categories: JSON.stringify([{ name: "New" }]) });
  });

  it("at launch when the lock is off here, and the leftover of an earlier one", async () => {
    await kept();
    closeCache();
    await sweep(false);
    await gone();
  });
});

describe("the share's window", () => {
  async function kept() {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    await settled();
  }
  const share = () => idb.dbs.get("runway-cache")!.get("rows")!.get("share")!;

  it("is the server's expiry, but never more than 72 hours by this device's clock (clock skew)", async () => {
    routes["POST /api/lock/key-share"] = () => shareReply(SHARE, NOW + 10 * HOUR);   // the server's clock is ahead
    await kept();
    expect(share()).toMatchObject({ fetchedAt: NOW, expiresAt: NOW + SHARE_WINDOW_MS });
    routes["POST /api/lock/key-share"] = () => shareReply(SHARE, NOW - 10 * HOUR);   // behind: its sooner time holds
    await relaunch();
    expect(share()).toMatchObject({ fetchedAt: NOW, expiresAt: NOW + SHARE_WINDOW_MS - 10 * HOUR });
  });

  it("is renewed by each unlock that gets the share", async () => {
    await kept();
    vi.setSystemTime(NOW + 70 * HOUR);
    await relaunch();
    expect(share()).toMatchObject({ fetchedAt: NOW + 70 * HOUR, expiresAt: NOW + 142 * HOUR });
    clearCache();
    expect(recall("categories")).toEqual([{ name: "Food" }]);
  });

  it("keeps what's there, unopened, when the share can't be fetched inside it (offline, too many asks)", async () => {
    await kept();
    for (const fail of [() => { throw new TypeError("Failed to fetch"); }, () => json({ error: "slow down" }, 429), () => json({ error: "down" }, 502)]) {
      routes["POST /api/lock/key-share"] = fail;
      vi.setSystemTime(NOW + 71 * HOUR);
      await relaunch();
      expect(isOpen()).toBe(false);   // no cache this time
      expect(recall("categories")).toBeUndefined();
      expect(records()).toEqual(["categories"]);
    }
    expect(lock.phase).toBe("unlocked");   // and the app opened as before
  });

  it("wipes everything once it's past and the share can't be fetched again", async () => {
    await kept();
    routes["POST /api/lock/key-share"] = () => { throw new TypeError("Failed to fetch"); };
    vi.setSystemTime(NOW + SHARE_WINDOW_MS);
    await relaunch();
    expect(idb.dbs.has("runway-cache")).toBe(false);
  });

  it("wipes everything at launch once it's past, and when the clock went back before the share was fetched", async () => {
    await kept();
    closeCache();
    vi.setSystemTime(NOW + SHARE_WINDOW_MS - 1);
    await sweep(true);
    expect(records()).toEqual(["categories"]);
    vi.setSystemTime(NOW + SHARE_WINDOW_MS);
    await sweep(true);
    expect(idb.dbs.has("runway-cache")).toBe(false);
    await kept();
    closeCache();
    vi.setSystemTime(NOW - 6 * 60 * 1000);
    await sweep(true);
    expect(idb.dbs.has("runway-cache")).toBe(false);
  });
});

describe("without a cache, the app works as before", () => {
  it("when the passkey gives no PRF secret (one made before, or a browser without PRF)", async () => {
    noteDataVersion("v1");
    await unlockWith(null);
    expect(lock.phase).toBe("unlocked");
    expect(isOpen()).toBe(false);
    expect(fetchMock.mock.calls.map((c) => c[0])).not.toContain("/api/lock/key-share");   // not even asked for
    remember("categories", [{ name: "Food" }]);
    await settled();
    expect(recall("categories")).toEqual([{ name: "Food" }]);   // memory as before
    expect(idb.dbs.has("runway-cache")).toBe(false);
  });

  it("when a cache was kept and the next unlock gives no PRF secret: it goes", async () => {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    await settled();
    await relaunch(null);
    expect(idb.dbs.has("runway-cache")).toBe(false);
  });

  it("when the browser has no IndexedDB: the PRF secret isn't even asked for", async () => {
    removeIndexedDB();
    await unlockWith(null);
    expect(vi.mocked(signChallenge).mock.calls[0][1]).toBeUndefined();
    expect(lock.phase).toBe("unlocked");
    remember("categories", [{ name: "Food" }]);
    expect(recall("categories")).toEqual([{ name: "Food" }]);
    await expect(forget()).resolves.toBeUndefined();
  });

  it("when IndexedDB won't open (a private window, blocked storage)", async () => {
    idb.failOpen = true;
    noteDataVersion("v1");
    await unlockWith();
    expect(lock.phase).toBe("unlocked");
    expect(isOpen()).toBe(false);
    expect(() => remember("categories", [{ name: "Food" }])).not.toThrow();
    expect(recall("categories")).toEqual([{ name: "Food" }]);
    await expect(forget()).resolves.toBeUndefined();
  });

  it("when the disk is full: writes stop quietly, and memory goes on", async () => {
    noteDataVersion("v1");
    await unlockWith();
    idb.failWrites = true;
    expect(() => remember("categories", [{ name: "Food" }])).not.toThrow();
    await settled();
    expect(recall("categories")).toEqual([{ name: "Food" }]);
    idb.failWrites = false;
    remember("accounts", [{ id: "a1" }]);   // nothing more this unlock
    await settled();
    expect(records()).toEqual([]);
  });
});

describe("how much it keeps", () => {
  it(`keeps at most ${MAX_RECORDS} records, the oldest going first`, async () => {
    noteDataVersion("v1");
    await unlockWith();
    for (let i = 0; i < MAX_RECORDS + 3; i++) {
      vi.setSystemTime(NOW + i * 1000);
      remember(`transactions?q=${i}`, { items: [], total: 0, sum: 0 });
      await settled();
    }
    const kept = records();
    expect(kept).toHaveLength(MAX_RECORDS);
    expect(kept).not.toContain("transactions?q=0");
    expect(kept).toContain(`transactions?q=${MAX_RECORDS + 2}`);
  });

  it("doesn't let a read from before a lock write after it", async () => {
    noteDataVersion("v1");
    await unlockWith();
    remember("categories", [{ name: "Food" }]);
    lockNow();   // before the sealed record reached the disk
    await settled();
    expect(records()).toEqual([]);
    await openCache(null);   // (and a stray open without a secret only wipes)
    expect(idb.dbs.has("runway-cache")).toBe(false);
  });
});

describe("holding the page back while it opens", () => {
  it("only when the passkey gave a PRF secret, until it's open, and OPENING_MS at most", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(NOW);
    let answer!: (r: Response) => void;
    routes["POST /api/lock/key-share"] = () => new Promise<Response>((r) => { answer = r; });
    lock.phase = "locked"; lock.launch = false;
    vi.mocked(signChallenge).mockResolvedValue({ answer: ANSWER, prf: new Uint8Array(PRF) });
    await unlock();
    while (!answer) await new Promise((r) => setImmediate(r));   // (WebCrypto answers outside the fake timers)
    expect(lock.phase).toBe("unlocked");
    expect(lock.opening).toBe(true);   // App shows its loading placeholder meanwhile
    vi.advanceTimersByTime(OPENING_MS - 1);
    expect(lock.opening).toBe(true);
    vi.advanceTimersByTime(1);
    expect(lock.opening).toBe(false);   // a slow share doesn't hold the app back any longer
    answer(json(shareReply()));
    await settled();
    expect(isOpen()).toBe(true);

    routes["POST /api/lock/key-share"] = () => shareReply();
    lockNow(false);
    expect(lock.opening).toBe(false);
    await unlockWith();   // answered in time: let go as soon as it's open
    expect(lock.opening).toBe(false);
    expect(isOpen()).toBe(true);

    lockNow(false);
    lock.phase = "locked";
    vi.mocked(signChallenge).mockResolvedValue({ answer: ANSWER, prf: null });
    await unlock();
    expect(lock.opening).toBe(false);   // no PRF secret: nothing to open, nothing held back
  });
});
