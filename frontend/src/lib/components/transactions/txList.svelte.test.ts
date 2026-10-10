// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));

import { api } from "$lib/api";
import { route } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { txFilters, txShow } from "$lib/filters.svelte";
import { category, tx } from "../../../test/fixtures";
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

  describe("after a change, only the rows it touched", () => {
    // A server with these rows: All's list (newest first, without what's marked Ignore), its count and sum, and ?id=.
    let rows: Tx[] = [];
    const serve = async (p: string) => {
      const qs = new URLSearchParams(p.split("?")[1]), ids = qs.getAll("id");
      const shown = rows.filter((t) => (qs.get("ignored") !== "0" || t.category !== "Ignore") && (!qs.get("category") || t.category === qs.get("category")))
        .sort((a, b) => b.posted.localeCompare(a.posted) || a.id.localeCompare(b.id));
      const items = shown.filter((t) => !ids.length || ids.includes(t.id))
        .slice(Number(qs.get("offset")), Number(qs.get("offset")) + Number(qs.get("limit")));
      return { items: structuredClone(items), total: shown.length, sum: shown.reduce((s, t) => s + t.amount, 0) } as TxList;
    };
    const day = (i: number) => `2026-03-${String(28 - i).padStart(2, "0")}`;
    const set = (id: string, change: Partial<Tx>) => { Object.assign(rows.find((t) => t.id === id)!, change); };
    const listed = async () => {
      vi.mocked(api).mockImplementation(serve as never);
      const l = make();
      await flush();
      return l;
    };
    beforeEach(() => {
      rows = Array.from({ length: 6 }, (_, i) => tx({ id: `t${i}`, posted: day(i), amount: -(i + 1), category: "Coffee" }));
      categories.list = [category("Coffee"), category("Groceries"), category("Ignore", { is_transfer: 1 }), category("Transfer", { is_transfer: 1 })];
    });
    afterEach(() => { categories.list = []; });

    it("asks for just those rows and agrees with loading the whole list again", async () => {
      const l = await listed();
      set("t1", { category: "Groceries", notes: "Market" }); set("t3", { category: "Groceries" });
      await l.reloadRows(["t1", "t3", "t1"], ["Coffee", "Groceries"]);
      expect(paths().at(-1)).toBe("/api/transactions?ignored=0&limit=2&offset=0&id=t1&id=t3");
      const whole = await serve("/api/transactions?ignored=0&limit=1000&offset=0");
      expect($state.snapshot(l.list)).toEqual(whole);
      expect([l.count, l.refreshError]).toEqual([6, ""]);
    });

    it("takes out a row that has left the filters, with the count and sum, as loading it all again would", async () => {
      txFilters.transactions.category = "Coffee";
      const l = await listed();
      set("t2", { category: "Groceries" });
      rows[0].amount = -50;   // (changed elsewhere: the count and sum are the server's, of rows not loaded again too)
      await l.reloadRows(["t2"], ["Coffee", "Groceries"]);
      expect(paths().at(-1)).toBe("/api/transactions?category=Coffee&ignored=0&limit=1&offset=0&id=t2");
      const whole = await serve("/api/transactions?category=Coffee&ignored=0&limit=1000&offset=0");
      expect(l.list?.items.map((t) => t.id)).toEqual(whole.items.map((t) => t.id));
      expect([l.count, l.list?.total, l.list?.sum]).toEqual([5, 5, -50 - 2 - 4 - 5 - 6]);
      expect(l.list?.items[0].amount).toBe(-1);   // a row it didn't touch stays as it was loaded
    });

    it("loads the whole list again when a few rows can't show the change", async () => {
      const whole = (l: TxListing) => expect(paths().at(-1), "whole list").toBe(`/api/transactions?ignored=0&limit=${Math.max(100, l.list!.items.length)}&offset=0`);
      const l = await listed();
      set("t1", { category: "Transfer" });   // a transfer category: another row's logo can change
      await l.reloadRows(["t1"], ["Coffee", "Transfer"]);
      whole(l);
      set("t2", { posted: "2026-03-29" });   // a new date: it moves
      await l.reloadRows(["t2"]);
      expect(paths().at(-2)).toContain("&id=t2");
      whole(l);
      expect(l.list?.items[0].id).toBe("t2");
      set("t3", { category: "Ignore" });   // (a transfer category too)
      await l.reloadRows(["t3"], ["Coffee", "Ignore"]);
      whole(l);
      rows.push(tx({ id: "new", posted: day(2), category: "Coffee" }));   // new to the list: where it goes is the server's
      await l.reloadRows(["new"]);
      expect(paths().at(-2)).toContain("&id=new");
      whole(l);
      await l.reloadRows(Array.from({ length: 101 }, (_, i) => `x${i}`));
      whole(l);
      l.f.q = "rent";   // filters being edited: the list loads with them
      await l.reloadRows(["t1"]);
      expect(paths().at(-1)).toContain("q=rent");
      expect(paths().at(-1)).not.toContain("id=");
    });

    it("loads the whole list while it's painted from memory, and remembers the patched one as a whole load would", async () => {
      const first = await listed();
      set("t1", { category: "Groceries" });
      await first.reloadRows(["t1"], ["Coffee", "Groceries"]);
      expect(paths().at(-1)).toContain("&id=t1");
      cleanup();
      let release!: (v: TxList) => void;
      vi.mocked(api).mockImplementationOnce((() => new Promise<TxList>((r) => { release = r; })) as never);
      const l = make();   // the next visit: painted from memory (the patched list), not yet confirmed
      expect([l.stale, l.list?.items[1].category]).toEqual([true, "Groceries"]);
      set("t2", { category: "Groceries" });
      vi.mocked(api).mockImplementation(serve as never);
      await l.reloadRows(["t2"], ["Coffee", "Groceries"]);   // a whole load: it confirms every row, not just t2
      expect(paths().at(-1)).toBe("/api/transactions?ignored=0&limit=100&offset=0");
      expect([l.stale, l.list?.items[2].category]).toEqual([false, "Groceries"]);
      release(await serve("/api/transactions?ignored=0&limit=100&offset=0"));   // the first refresh, overtaken: ignored
      await flush();
      expect($state.snapshot(l.list)).toEqual(await serve("/api/transactions?ignored=0&limit=1000&offset=0"));
    });

    it("keeps the list, and says it isn't up to date, when loading the rows fails; a newer load wins", async () => {
      const l = await listed();
      vi.mocked(api).mockRejectedValueOnce(new Error("Down"));
      await l.reloadRows(["t1"]);
      expect([l.list?.items.length, l.refreshError]).toEqual([6, "Down"]);
      let slow!: (v: TxList) => void;
      vi.mocked(api).mockImplementationOnce((() => new Promise<TxList>((r) => { slow = r; })) as never);
      const late = l.reloadRows(["t1"]);
      set("t1", { category: "Groceries" });
      await l.load();
      expect(l.refreshError).toBe("");
      slow({ items: [], total: 0, sum: 0 } as TxList);
      await late;
      expect(l.list?.items).toHaveLength(6);
      expect(l.list?.items[1].category).toBe("Groceries");
    });
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
