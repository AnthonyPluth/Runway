// To review's own behaviour on the Transactions page: moving through the queue with the keyboard, keeping focus where
// you were when a row leaves it, accepting what's certain at once, and reviewing a merchant at a time.
import { errMsg } from "$lib/act";
import { api } from "$lib/api";
import { refreshState } from "$lib/app.svelte";
import { categories } from "$lib/categories.svelte";
import { plural } from "$lib/format";
import { undoable } from "$lib/undo";
import { tick } from "svelte";
import { toast } from "svelte-sonner";
import { restoreTx, type Was } from "./restore";
import { reviewAction } from "./reviewKeys";
import type { TxListing } from "./txList.svelte";
import type { Tx } from "./types";

/** More than this many at once asks before "Accept all". */
const CONFIRM_AT = 10;

const rowEl = (id: string) => [...document.querySelectorAll<HTMLElement>("[data-tx]")].find((r) => r.dataset.tx === id);

export class ReviewMode {
  readonly txs: TxListing;
  private on: { accept: (t: Tx) => Promise<boolean>; save: (t: Tx, category: string) => Promise<boolean> };
  /** The row the keyboard is on (j/k), by id; "" when none is. */
  keyed = $state("");
  /** Review a merchant at a time (without an AI key, which does that with suggestions). Off unless you turn it on. */
  grouped = $state(false);
  askAll = $state(false);
  heading = $state<HTMLElement>();
  caughtUp = $state<HTMLElement>();

  // "Accept all ≥ 90%": the loaded rows waiting with a category it's sure of (all of them with a category, when there
  // are no confidences to go by). More than CONFIRM_AT asks first.
  readonly confident: Tx[];
  readonly sure: boolean;
  readonly acceptable: Tx[];

  /** `accept` and `save` are the page's (they go through its optimistic saves). */
  constructor(txs: TxListing, on: { accept: (t: Tx) => Promise<boolean>; save: (t: Tx, category: string) => Promise<boolean> }) {
    this.txs = txs; this.on = on;
    this.confident = $derived((txs.list?.items ?? []).filter((t) => t.needs_review && t.category && !t.is_split && (t.confidence ?? 0) >= 0.9));
    this.sure = $derived((txs.list?.items ?? []).some((t) => t.needs_review && t.category && t.confidence != null));
    this.acceptable = $derived(this.sure ? this.confident : (txs.list?.items ?? []).filter((t) => t.needs_review && t.category && !t.is_split));
  }

  /** After a transaction leaves the list, keyboard focus goes to the next row's category (else the row before it), or,
   * with none left, to "All caught up": without this it falls to the page and you start again from the top. Moving with
   * the keys (j/k), the row itself has the focus (and the ring), so Enter is Review's. */
  focusAfter = async (id: string | undefined): Promise<void> => {
    await tick();
    if (this.keyed) this.keyed = id ?? "";
    const row = id ? rowEl(id) : undefined;
    if (row && this.keyed) { row.tabIndex = -1; row.focus(); row.scrollIntoView?.({ block: "nearest" }); return; }
    (row?.querySelector<HTMLElement>("[data-category-trigger]") ?? this.caughtUp ?? this.heading)?.focus();
  };

  onkey = (e: KeyboardEvent): void => {
    const list = this.txs.list;
    if (!this.txs.review || !list?.items.length || this.grouped) return;
    const action = reviewAction(e, !!this.keyed && !!list.items.find((x) => x.id === this.keyed));
    if (!action) return;
    const items = list.items, at = items.findIndex((x) => x.id === this.keyed), t = at >= 0 ? items[at] : undefined;
    e.preventDefault();
    if (action === "next" || action === "prev") {
      const to = at < 0 ? 0 : Math.max(0, Math.min(items.length - 1, at + (action === "next" ? 1 : -1)));
      this.keyed = items[to].id; this.focusAfter(this.keyed);
    } else if (action === "clear") {
      this.keyed = ""; (document.activeElement as HTMLElement | null)?.blur?.();
    } else if (t) {
      const row = rowEl(t.id);
      if (action === "enter") { if (t.category && t.needs_review && !t.is_split) this.on.accept(t); else row?.querySelector<HTMLElement>("[data-category-trigger]")?.click(); }
      else if (action === "pick") row?.querySelector<HTMLElement>("[data-category-trigger]")?.click();
      else if (action === "split") row?.querySelector<HTMLElement>("[data-split]")?.click();
      else if (action === "ignore" || action === "transfer") {
        const name = action === "ignore" ? "Ignore" : "Transfer";
        if (categories.list.some((c) => c.name === name)) this.on.save(t, name); else toast.error(`There’s no ${name} category`);
      }
    }
  };

  acceptAll = async (): Promise<boolean> => {
    const txs = this.txs, list = txs.list;
    if (!list) return false;
    const rows = [...this.acceptable], ids = new Set(rows.map((t) => t.id));
    const before = list.items.map((t) => t.id);
    list.items = list.items.filter((t) => !ids.has(t.id));
    txs.count = Math.max(0, txs.count - rows.length); list.total = Math.max(0, list.total - rows.length);
    try {
      const r = await api<{ updated: number; was: Was[] }>("/api/transactions/bulk", { method: "POST", body: { ids: [...ids], reviewed: true } });
      undoable(`Accepted ${plural(r.updated, "transaction")}`, async () => { await restoreTx(r.was); await txs.load(); });
      refreshState();
      if (!txs.list?.items.length) await txs.load();
      return true;
    } catch (err) {
      // Back where they were.
      const now = txs.list;
      if (now) {
        const byId = new Map([...rows, ...now.items].map((t) => [t.id, t]));
        now.items = before.map((id) => byId.get(id)).filter((t): t is Tx => !!t);
        now.total += rows.length;
      }
      txs.count += rows.length;
      toast.error(errMsg(err));
      return false;
    }
  };
  startAcceptAll = (): void => { if (this.acceptable.length > CONFIRM_AT) this.askAll = true; else this.acceptAll(); };

  /** Some merchants' rows were decided in the grouped view. */
  groupApplied = (ids: string[]): void => {
    if (!this.txs.list) return;
    this.txs.drop(ids);
    if (!this.txs.list.items.length) this.txs.load();
  };
}
