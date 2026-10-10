// @vitest-environment jsdom
// The in-memory cache of last-seen lists (lib/swr.ts): what it keeps, what it holds back, and everything that empties it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() }) }));

import { staleAccounts, loadAccounts } from "./accounts";
import { api, ApiError } from "./api";
import { loadRecurring, staleRecurring } from "./components/recurring/load";
import { forget, lock, lockNow } from "./lock.svelte";
import { cacheEpoch, clearCache, recall, remember } from "./swr";

const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { vi.useFakeTimers(); fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); clearCache(); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); lock.phase = "off"; });

describe("the cache", () => {
  it("hands back copies, so neither the caller nor the cache can change the other", () => {
    const rows = [{ id: 1 }];
    remember("k", rows);
    rows[0].id = 2;
    const got = recall<{ id: number }[]>("k")!;
    expect(got).toEqual([{ id: 1 }]);
    got[0].id = 3;
    expect(recall("k")).toEqual([{ id: 1 }]);
    expect(recall("other")).toBeUndefined();
  });

  it("does not fail what it follows when a copy can't be made", () => {
    remember("k", [1]);
    expect(() => remember("k", [() => 1])).not.toThrow();   // functions can't be cloned
    expect(recall("k")).toBeUndefined();
  });

  it("does not take a reply whose read began before it was emptied", () => {
    const at = cacheEpoch();
    clearCache();
    remember("k", [1], at);
    expect(recall("k")).toBeUndefined();
    remember("k", [1]);
    expect(recall("k")).toEqual([1]);
  });
});

describe("what it keeps of a list", () => {
  it("leaves out balances and statements from the accounts, and due dates and what's missed from recurring items", async () => {
    fetchMock.mockImplementation((path: string) => reply(path === "/api/accounts"
      ? [{ id: "a1", name: "Checking", kind: "checking", hidden: 0, balance: 1234.5, available: 1000, balance_date: "2026-10-09", statement: { due: "x" }, statements: [{}] }]
      : [{ id: 7, name: "Rent", amount: -900, frequency: "monthly", active: 1, matched_count: 3, next_date: "2026-11-01", late_date: "2026-10-01",
        expected_amount: -905, suggested_amount: -910, skipped: ["2026-12-01"], missed: [{ key: "m" }] }]));
    expect((await loadAccounts())[0].balance).toBe(1234.5);   // the live load is whole
    await loadRecurring();
    const [a] = staleAccounts()!;
    expect(a).toMatchObject({ id: "a1", name: "Checking", kind: "checking", hidden: false, balance: null, available: null, balance_date: null, statement: null });
    expect(staleRecurring()).toEqual([{ id: 7, name: "Rent", amount: -900, frequency: "monthly", active: 1, matched_count: 3 }]);
  });
});

describe("what empties it", () => {
  const fill = () => remember("k", [1]);

  it("locking the app (Lock now, or the server's 423)", () => {
    fill(); lock.phase = "unlocked";
    lockNow(false);
    expect(recall("k")).toBeUndefined();
  });

  it("signing out, or the lock being forgotten on this device", () => {
    fill();
    forget();
    expect(recall("k")).toBeUndefined();
  });

  it("a 401", async () => {
    fill();
    fetchMock.mockReturnValue(reply({}, 401));
    await expect(api("/api/x", { background: true })).rejects.toBeInstanceOf(ApiError);
    expect(recall("k")).toBeUndefined();
  });

  it("a change made through the API, even one that failed, but not a read or Runway checking in", async () => {
    fill();
    fetchMock.mockReturnValue(reply({ ok: true }));
    await api("/api/x");
    await api("/api/sync/auto", { method: "POST", background: true });
    expect(recall("k")).toEqual([1]);
    await api("/api/x", { method: "POST", body: {} });
    expect(recall("k")).toBeUndefined();
    fill();
    fetchMock.mockReturnValue(reply({ error: "no" }, 500));
    await expect(api("/api/x", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    expect(recall("k")).toBeUndefined();
  });

  it("a change whose request never got an answer (a dropped connection), which may still have reached the server", async () => {
    fill();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/api/x", { method: "DELETE" })).rejects.toMatchObject({ status: 0 });
    expect(recall("k")).toBeUndefined();
  });

  it("keeps what an earlier read began from filling it again after the change", async () => {
    let answer!: (r: Response) => void;
    fetchMock.mockImplementation((path: string) => path === "/api/accounts" ? new Promise<Response>((r) => { answer = r; }) : reply({ ok: true }));
    const reading = loadAccounts();
    await api("/api/accounts/edit", { method: "POST" });   // a change while the read was out
    answer(new Response(JSON.stringify([{ id: "a1", name: "Checking", kind: "checking", hidden: 0 }])));
    await reading;
    expect(staleAccounts()).toBeUndefined();
  });
});

describe("emptied together with the replies kept for 304s (lib/api.ts)", () => {
  const tagged = () => Promise.resolve(new Response("[1]", { headers: { ETag: '"a"' } }));
  // Both kept: a list remembered for instant paint, and a reply with its ETag (the next read asks with it).
  const fill = async () => { remember("k", [1]); fetchMock.mockReturnValueOnce(tagged()); await api("/api/k", { keep: true }); };
  const bothGone = async () => {
    expect(recall("k")).toBeUndefined();
    fetchMock.mockReturnValueOnce(tagged());
    await api("/api/k", { keep: true });
    expect(fetchMock.mock.calls.at(-1)![1].headers).not.toHaveProperty("If-None-Match");
  };

  it("on locking, signing out (the lock forgotten) and a 401", async () => {
    await fill(); lock.phase = "unlocked";
    lockNow(false);
    await bothGone();
    await fill();
    forget();
    await bothGone();
    await fill();
    fetchMock.mockReturnValueOnce(reply({}, 401));
    await expect(api("/api/x", { background: true })).rejects.toBeInstanceOf(ApiError);
    await bothGone();
  });

  it("but a change keeps the ETag replies (the server still checks each one), and a read revalidates with it", async () => {
    await fill();
    fetchMock.mockReturnValueOnce(reply({ ok: true }));
    await api("/api/x", { method: "POST", body: {} });
    expect(recall("k")).toBeUndefined();
    fetchMock.mockReturnValueOnce(Promise.resolve(new Response(null, { status: 304, headers: { ETag: '"a"' } })));
    expect(await api("/api/k", { keep: true })).toEqual([1]);
    expect(fetchMock.mock.calls.at(-1)![1].headers).toHaveProperty("If-None-Match", '"a"');
  });
});
