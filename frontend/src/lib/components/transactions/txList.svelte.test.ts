// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { route } from "$lib/app.svelte";
import { txFilters, txShow } from "$lib/filters.svelte";
import { tx } from "../../../test/fixtures";
import { TxListing } from "./txList.svelte";
import type { Tx, TxList } from "./types";

const none = () => ({ q: "", account: "", category: "", from: "", to: "", min: "", max: "", kind: "" as const, scope: "" });
const page = (items: Tx[], total = items.length): TxList => ({ items, total, sum: 0 }) as TxList;
const paths = () => vi.mocked(api).mock.calls.map((c) => c[0] as string).filter((p) => !p.includes("ignored=only"));
const flush = () => vi.advanceTimersByTimeAsync(0);
let cleanup = () => {};
const make = (review = false) => {
  let listing!: TxListing;
  cleanup = $effect.root(() => { listing = new TxListing(review); });
  return listing;
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
  Object.assign(txFilters.transactions, none()); Object.assign(txFilters.review, none());
  txShow.ignored = false;
  route.query = ""; route.page = "transactions";
  history.replaceState(null, "", "/#transactions");
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("TxListing", () => {
  it("loads the first page on creation, keeping what All hides out of it, and counts what's ignored", async () => {
    vi.mocked(api).mockImplementation((async (p: string) => (p.includes("ignored=only") ? page([], 4) : page([tx({ id: "a" }), tx({ id: "b" })], 2))) as never);
    const l = make();
    await flush();
    expect(paths()[0]).toBe("/api/transactions?ignored=0&limit=100&offset=0");
    expect(l.list?.items.map((t) => t.id)).toEqual(["a", "b"]);
    expect(l.count).toBe(2);
    expect(l.ignoredCount).toBe(4);
    expect(l.loads).toBe(1);
  });

  it("asks for the review queue in To review, with no ignored line", async () => {
    vi.mocked(api).mockResolvedValue(page([tx({ id: "a" })]) as never);
    const l = make(true);
    await flush();
    expect(paths()).toEqual(["/api/transactions?review=1&limit=100&offset=0"]);
    expect(l.ignoredCount).toBe(0);
  });

  it("keeps the list on screen, and says so, when loading it again fails; a first failure is the list's own", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Down"));
    const l = make();
    await flush();
    expect(l.list).toBeNull();
    expect(l.listError).toBe("Down");
    vi.mocked(api).mockResolvedValue(page([tx({ id: "a" })]) as never);
    await l.load();
    expect(l.listError).toBe("");
    vi.mocked(api).mockRejectedValue(new Error("Still down"));
    await l.load();
    expect(l.list?.items).toHaveLength(1);
    expect(l.refreshError).toBe("Still down");
    expect(l.listError).toBe("");
  });

  it("never lets a slow old answer replace a newer one", async () => {
    let slow!: (v: TxList) => void;
    vi.mocked(api).mockImplementationOnce((() => new Promise<TxList>((r) => { slow = r; })) as never);
    const l = make();
    vi.mocked(api).mockResolvedValue(page([tx({ id: "new" })]) as never);
    await l.load();
    slow(page([tx({ id: "old" })]));
    await flush();
    expect(l.list?.items.map((t) => t.id)).toEqual(["new"]);
  });

  it("reloads as many rows as were showing after a change with the same filters, and starts afresh for new ones", async () => {
    const rows = Array.from({ length: 100 }, (_, i) => tx({ id: `t${i}` }));
    vi.mocked(api).mockResolvedValue(page(rows, 150) as never);
    const l = make();
    await flush();
    vi.mocked(api).mockResolvedValue(page([...rows, tx({ id: "extra" })], 151) as never);
    await l.more();
    expect(l.list?.items).toHaveLength(101);
    await l.load();
    expect(paths().at(-1)).toContain("limit=101");
    expect(l.loads).toBe(1);
    l.f.account = "a1";
    await l.load();
    expect(paths().at(-1)).toContain("limit=100");
    expect(l.loads).toBe(2);
  });

  it("adds the next page without repeating a row that moved up meanwhile", async () => {
    vi.mocked(api).mockResolvedValue(page([tx({ id: "a" }), tx({ id: "b" })], 4) as never);
    const l = make();
    await flush();
    vi.mocked(api).mockResolvedValue(page([tx({ id: "b" }), tx({ id: "c" })], 4) as never);
    await l.more();
    expect(paths().at(-1)).toContain("offset=2");
    expect(l.list?.items.map((t) => t.id)).toEqual(["a", "b", "c"]);
  });

  it("searches once you pause, and a filter chosen meanwhile loads at once without a second search", async () => {
    vi.mocked(api).mockResolvedValue(page([]) as never);
    const l = make();
    await flush();
    const before = paths().length;
    l.search("r"); l.search("re"); l.search("rent");
    await vi.advanceTimersByTimeAsync(249);
    expect(paths()).toHaveLength(before);
    await vi.advanceTimersByTimeAsync(1);
    expect(paths()).toHaveLength(before + 1);
    expect(paths().at(-1)).toContain("q=rent");
    l.search("rentx");
    l.setMore({ kind: "out" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(paths()).toHaveLength(before + 2);
    expect(paths().at(-1)).toContain("kind=out");
  });

  it("writes All's filters into the address, and takes the ones in it on opening", async () => {
    route.query = "q=rent&kind=out";
    vi.mocked(api).mockResolvedValue(page([]) as never);
    const l = make();
    await flush();
    expect(l.f.q).toBe("rent");
    expect(paths()[0]).toContain("q=rent");
    expect(route.query).toBe("q=rent&kind=out");
  });

  it("takes rows out of the list and the counts without loading again", async () => {
    vi.mocked(api).mockResolvedValue(page([tx({ id: "a" }), tx({ id: "b" }), tx({ id: "c" })]) as never);
    const l = make(true);
    await flush();
    const calls = vi.mocked(api).mock.calls.length;
    l.drop(["a", "c", "nope"]);
    expect(l.list?.items.map((t) => t.id)).toEqual(["b"]);
    expect([l.count, l.list?.total]).toEqual([1, 1]);
    expect(vi.mocked(api).mock.calls).toHaveLength(calls);
  });
});
