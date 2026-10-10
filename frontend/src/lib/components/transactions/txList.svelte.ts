// The Transactions page's list: its filters (and the address that mirrors them), loading it, loading more of it, and
// loading it again after a change. The page's All and To review tabs each have one. Modelled on reports/chart.svelte.ts's
// Report: the last list stays on screen while a new one loads, and a slow old answer never replaces a newer one.
import { errMsg } from "$lib/act";
import { categories } from "$lib/categories.svelte";
import { apiCall } from "$lib/contract";
import { route, setQuery } from "$lib/app.svelte";
import { debounced } from "$lib/debounce";
import { clearAll, fromQuery, isFiltered, sameFilters, toQuery, txFilters, txShow, type TxFilters } from "$lib/filters.svelte";
import { untrack } from "svelte";
import type { TxList } from "./types";

export const PAGE = 100;
/** At most this many rows are loaded again on their own after a change (the server's transactions.MAX_IDS). */
export const MAX_IDS = 100;

export class TxListing {
  readonly review: boolean = false;
  /** The filters being edited (the tab's own, kept while you move around). */
  readonly f: TxFilters;
  list = $state<TxList | null>(null);
  /** The list couldn't be loaded at all. */
  listError = $state("");
  /** It couldn't be loaded again: the old one stays, marked as not up to date. */
  refreshError = $state("");
  /** The count in the heading (in Review, goes down as you categorize). */
  count = $state(0);
  /** A new search starts the table afresh (no leftover ticks); a reload after a change keeps it. */
  loads = $state(0);
  /** The filters the list (and Upcoming) was last loaded with. */
  applied = $state<TxFilters>({} as TxFilters);
  appliedIgnored = $state(txShow.ignored);
  /** What's marked Ignore under the same search and filters (All only, without a category filter); null while it isn't
   * known (it couldn't be counted), when the line still offers to show them. */
  ignoredCount = $state<number | null>(0);
  filtered = $derived(isFiltered(this.applied));
  /** Every transaction the list shows, for a change to all of them at once ("Select all 212"): its filters as loaded. */
  everyFilter = $derived({
    ...Object.fromEntries(this.params(this.applied)),
    ...(this.review ? { review: "1" } : this.appliedIgnored ? {} : { ignored: "0" }),
  });
  #seq = 0;
  #searching = debounced(() => this.load(), 250);

  constructor(review: boolean) {
    this.review = review;
    this.f = review ? txFilters.review : txFilters.transactions;
    // All's filters are in the address too (#transactions?q=…): opened from one, it has those filters; opened plainly
    // (the sidebar), the ones from last time, written into the address.
    if (!review) {
      if (route.query) Object.assign(this.f, fromQuery(route.query));
      setQuery(toQuery(this.f));
    }
    this.applied = { ...this.f };
    // Back, Forward or a link to other filters while the page is open: load those.
    $effect(() => {
      const q = route.query;   // only the address: what you type changes the filters first, and the address after
      untrack(() => {
        if (review || route.page !== "transactions" || q === toQuery(this.f)) return;
        const next = fromQuery(q);
        if (sameFilters(next, this.f)) return;
        this.#searching.cancel(); Object.assign(this.f, next); this.load();
      });
    });
    this.load();
  }

  /** The filters that are set, as the list's query. */
  params(now: TxFilters): URLSearchParams {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(now)) if (v) qs.set(k, v);
    return qs;
  }
  // The list's own conditions besides the filters: Review's queue, or All without what's marked Ignore.
  #scope(): Record<string, string> {
    return this.review ? { review: "1" } : txShow.ignored ? {} : { ignored: "0" };   // what's marked Ignore stays out of All unless asked for
  }
  #query(offset: number, limit = PAGE) {
    const qs = this.params(this.f);
    for (const [k, v] of Object.entries(this.#scope())) qs.set(k, v);
    qs.set("limit", String(limit)); qs.set("offset", String(offset));
    return qs;
  }

  async #countIgnored(mine: number, now: TxFilters) {
    if (this.review) return;
    if (now.category) { this.ignoredCount = 0; return; }
    const qs = this.params(now);
    qs.set("ignored", "only"); qs.set("limit", "1"); qs.set("offset", "0");
    try {
      const r = await apiCall<"GET /api/transactions">(`/api/transactions?${qs}`);
      if (mine === this.#seq) this.ignoredCount = r.total;
    } catch { if (mine === this.#seq) this.ignoredCount = null; }
  }

  load = async (): Promise<void> => {
    const mine = ++this.#seq;
    const now = { ...this.f }, ignored = txShow.ignored;
    if (!this.review) setQuery(toQuery(now));
    // After a change with the same filters, load as many as were showing, so the list (and where you are in it) stays.
    const same = !!this.list && ignored === this.appliedIgnored && (Object.keys(now) as (keyof TxFilters)[]).every((k) => now[k] === this.applied[k]);
    const qs = this.#query(0, same ? Math.min(1000, Math.max(PAGE, this.list!.items.length)) : PAGE);
    try {
      const data = await apiCall<"GET /api/transactions">(`/api/transactions?${qs}`);
      if (mine !== this.#seq) return;   // a newer search has been asked for meanwhile
      // The same search again (after a change): the rows are updated where they are, so nothing redraws or jumps. A new
      // search starts the table afresh.
      this.applied = now; this.appliedIgnored = ignored; this.list = data; this.count = data.total; this.listError = ""; this.refreshError = "";
      if (!same) this.loads++;
      this.#countIgnored(mine, now);
    } catch (err) {
      if (mine !== this.#seq) return;
      // With a list on screen it stays (you may be part way down it), under a line saying it isn't up to date.
      if (this.list) this.refreshError = errMsg(err); else this.listError = errMsg(err);
    }
  };

  /** After a change to these transactions (the ids in its reply's `was`), only they are loaded again, under the filters
   * the list was loaded with: each is updated where it is, one that has left the filters leaves the list, and the count
   * and sum are the whole list's. `cats`: the categories the change was from and to. When a few rows can't show the
   * change it loads the whole list again instead: filters edited since, too many rows, a row new to the list or with
   * another date (it would move), or a transfer category (it can change another row: the card a payment's logo shows). */
  reloadRows = async (ids: string[], cats: (string | null | undefined)[] = []): Promise<void> => {
    const list = this.list, now = { ...this.f }, ignored = txShow.ignored, want = [...new Set(ids)];
    const transfer = (c: string | null | undefined) => !!c && !!categories.list.find((x) => x.name === c)?.is_transfer;
    if (!list || !want.length || want.length > MAX_IDS || ignored !== this.appliedIgnored || !sameFilters(now, this.applied) ||
        cats.some(transfer)) return this.load();
    const mine = ++this.#seq;
    const qs = this.#query(0, want.length);
    for (const id of want) qs.append("id", id);
    try {
      const data = await apiCall<"GET /api/transactions">(`/api/transactions?${qs}`);
      if (mine !== this.#seq || this.list !== list) return;
      const at = new Map(list.items.map((t) => [t.id, t.posted]));
      if (data.items.some((t) => at.get(t.id) !== t.posted)) return this.load();
      const fresh = new Map(data.items.map((t) => [t.id, t]));
      list.items = list.items.filter((t) => fresh.has(t.id) || !want.includes(t.id)).map((t) => fresh.get(t.id) ?? t);
      list.total = data.total; list.sum = data.sum; this.count = data.total; this.listError = ""; this.refreshError = "";
      this.#countIgnored(mine, now);
    } catch (err) {
      if (mine === this.#seq) this.refreshError = errMsg(err);
    }
  };

  /** The next page, added to the end (skipping any that shifted in since, e.g. after a sync). A failure is the table's
   * to show (with a Retry). */
  more = async (): Promise<void> => {
    if (!this.list) return;
    const mine = this.#seq, have = new Set(this.list.items.map((t) => t.id));
    const data = await apiCall<"GET /api/transactions">(`/api/transactions?${this.#query(this.list.items.length)}`);
    if (mine !== this.#seq || !this.list) return;
    this.list.items.push(...data.items.filter((t) => !have.has(t.id)));
    this.list.total = data.total;
  };

  /** Typing in the search box: loads once you pause. */
  search = (v: string) => { this.f.q = v; this.#searching.call(); };
  setDates = (from: string, to: string) => { this.#searching.cancel(); this.f.from = from; this.f.to = to; if (!from && !to) this.f.scope = ""; this.load(); };
  setMore = (next: Partial<TxFilters>) => { this.#searching.cancel(); Object.assign(this.f, next); this.load(); };
  clearFilters = () => { this.#searching.cancel(); clearAll(this.f); this.load(); };

  /** Takes rows out of the list (and the counts) without loading again: they've left, as in Review once decided. */
  drop(ids: Iterable<string>): void {
    if (!this.list) return;
    const gone = new Set(ids), was = this.list.items.length;
    this.list.items = this.list.items.filter((t) => !gone.has(t.id));
    const n = was - this.list.items.length;
    this.count = Math.max(0, this.count - n); this.list.total = Math.max(0, this.list.total - n);
  }
}
