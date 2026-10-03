<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import { categories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { Input } from "$lib/components/ui/input";
  import { fmtDate, fmtSigned, plural } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import { restoreTx, type Was } from "./restore";
  import TxRow from "./TxRow.svelte";
  import type { Tx } from "./types";

  // The list, a day at a time, with checkboxes (shift-click for a range) to change many transactions together: a
  // category, the merchant's name, or marking them reviewed. The bar for that sticks to the top while you scroll. Changing
  // CONFIRM_AT or more asks first, saying how many; every change can be undone from its toast.
  // More load as you reach the bottom (`onmore`). On a phone the checkboxes show once you tap Select.
  // `only`: the category the list is filtered by. A split transaction then counts (and shows) only its part in it, and a
  // category set on it changes only that part. `family`: that category and its subcategories, for the receipts.
  // `oneAccount`: filtered to one account, so the rows leave it out.
  let { items, total, review, recurring, only = "", family, oneAccount = false, selecting = $bindable(false), onsave, onchanged, onmore }: {
    items: Tx[]; total: number; review: boolean; recurring: RecurringItem[]; only?: string; family?: string[]; oneAccount?: boolean; selecting?: boolean;
    onsave: (t: Tx, category: string) => Promise<void>; onchanged: () => void; onmore?: () => Promise<void>;
  } = $props();

  // What a transaction adds to its day's total: its amount, less the parts in a transfer category (card payments,
  // moves between accounts), which show on both sides and aren't money in or out.
  const isTransfer = (name: string | null | undefined) => !!name && !!categories.list.find((c) => c.name === name)?.is_transfer;
  const counted = (t: Tx) => (t.match ? t.match.amount : t.splits?.length ? t.splits.reduce((n, p) => n + (isTransfer(p.category) ? 0 : p.amount), 0)
    : isTransfer(t.category) ? 0 : t.amount);

  // Days, newest first, each with what came in and went out that day.
  const days = $derived.by(() => {
    const out: { day: string; rows: { t: Tx; i: number }[]; net: number }[] = [];
    items.forEach((t, i) => {
      const day = t.posted.slice(0, 10);
      if (out.at(-1)?.day !== day) out.push({ day, rows: [], net: 0 });
      const d = out.at(-1)!;
      d.rows.push({ t, i }); d.net += counted(t);
    });
    return out;
  });
  const thisYear = String(new Date().getFullYear());
  const dayLabel = (d: string) => fmtDate(d, d.startsWith(thisYear) ? { weekday: "long", month: "short", day: "numeric" }
    : { weekday: "short", month: "short", day: "numeric", year: "numeric" });

  // Load more when the end of the list comes into view (the button does the same by hand).
  let more = $state(false);
  let end = $state<HTMLElement>();
  async function loadMore() {
    if (more || !onmore || items.length >= total) return;
    more = true;
    try { await onmore(); } finally { more = false; }
  }
  $effect(() => {
    if (!end || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((e) => { if (e.some((x) => x.isIntersecting)) loadMore(); }, { rootMargin: "600px" });
    io.observe(end);
    return () => io.disconnect();
  });

  let picked = $state<Record<string, boolean>>({});
  let last: number | null = null;
  const ids = $derived(items.filter((t) => picked[t.id]).map((t) => t.id));
  const sum = $derived(items.reduce((n, t) => n + (picked[t.id] ? (t.match?.amount ?? t.amount) : 0), 0));
  let bulkCat = $state("");
  let rename = $state("");

  function select(e: MouseEvent, i: number, checked: boolean) {
    if (e.shiftKey && last !== null) {   // shift-click: everything between the last tick and this one
      for (let k = Math.min(last, i); k <= Math.max(last, i); k++) picked[items[k].id] = checked;
    } else picked[items[i].id] = checked;
    last = i;
  }
  function all(checked: boolean) { for (const t of items) picked[t.id] = checked; }

  // A change to many at once asks first from here up; fewer just happens (and can be undone).
  const CONFIRM_AT = 10;
  type Change = { body: Record<string, unknown>; what: string; ids: string[]; title: string; description: string; confirm: string; busy: string };
  let asking = $state(false);
  let pending = $state<Change | null>(null);
  $effect(() => { if (!asking) bulkCat = ""; });   // backing out leaves "Set category…" showing, not the one that was asked about

  async function send(c: Pick<Change, "body" | "what" | "ids">): Promise<boolean> {
    try {
      const r = await api<{ updated: number; was: Was[] }>("/api/transactions/bulk", { method: "POST", body: { ids: c.ids, ...c.body } });
      undoable(`${c.what} · ${plural(r.updated, "transaction")}`, async () => { await restoreTx(r.was); onchanged(); });
      picked = {}; bulkCat = ""; rename = "";   // done with these; the list below updates where it is
      refreshState(); onchanged();
      return true;
    } catch (err) { toast.error((err as Error).message); return false; }
  }
  function change(c: Omit<Change, "ids" | "description" | "confirm" | "busy">, description: string, confirm: string, busy: string) {
    const all = { ...c, ids: [...ids], description, confirm, busy };
    if (all.ids.length < CONFIRM_AT) send(all);
    else { pending = all; asking = true; }
  }
  const setCategory = (v: string) => change({ body: only ? { category: v, only } : { category: v }, what: `Set to ${v}`, title: `Set ${v} on ${plural(ids.length, "transaction")}?` },
    only ? `They leave To review. A split one changes only its ${only} part. You can undo it afterwards.`
      : "They leave To review, and any split ones go back to a single category. You can undo it afterwards.", "Set category", "Setting…");
  function doRename() {
    const v = rename.trim();
    if (v) change({ body: { payee: v }, what: `Renamed to ${v}`, title: `Rename ${plural(ids.length, "transaction")} to ${v}?` },
      "Only the name shown here changes, not the bank’s own text. You can undo it afterwards.", "Rename", "Renaming…");
  }
  const markReviewed = () => change({ body: { reviewed: true }, what: "Marked reviewed", title: `Mark ${plural(ids.length, "transaction")} reviewed?` },
    "They keep their categories and leave To review. You can undo it afterwards.", "Mark reviewed", "Marking…");
</script>

<div>
  {#if ids.length}
    <div data-editor class="sticky top-[env(safe-area-inset-top)] z-10 mb-3 flex flex-wrap items-center gap-2.5 rounded-lg border bg-popover p-2.5 text-sm shadow-md" role="region" aria-label="Change the selected transactions">
      <span class="tabular-nums"><b>{ids.length} selected</b> <span class="text-muted-foreground">{fmtSigned(sum)}</span></span>
      <CategorySelect bind:value={bulkCat} blank="Set category…" label="Category for the selected transactions" class="w-48"
        onchange={(v) => v && setCategory(v)} />
      <span class="flex items-center gap-1.5">
        <Input bind:value={rename} placeholder="Rename merchant to…" aria-label="New merchant name" class="w-48"
          onkeydown={(e) => { if (e.key === "Enter") doRename(); }} />
        <Button variant="outline" size="sm" onclick={doRename}>Rename</Button>
      </span>
      <Button variant="outline" size="sm" title="Keep their categories and take them out of Review"
        onclick={markReviewed}>Mark reviewed</Button>
      <Button variant="link" size="sm" onclick={() => all(false)}>Clear selection</Button>
    </div>
  {/if}

  <div class="mb-2 flex items-center gap-3 px-4 text-[13px] text-muted-foreground">
    <label class={cn("flex items-center", !selecting && "max-md:hidden")}>
      <input type="checkbox" class="size-4 cursor-pointer accent-primary" aria-label="Select all shown"
        checked={ids.length > 0 && ids.length === items.length} indeterminate={ids.length > 0 && ids.length < items.length}
        onchange={(e) => all(e.currentTarget.checked)} />
    </label>
    <button type="button" class="ml-auto cursor-pointer text-[15px] text-primary md:hidden"
      onclick={() => { selecting = !selecting; if (!selecting) all(false); }}>{selecting ? "Done" : "Select"}</button>
  </div>
  <!-- A grouped list per day, its heading staying in view while you scroll through that day. From lg up it is one
       container, the days thin sub-headers in it. -->
  <div data-tx-list class="flex flex-col gap-4 lg:gap-0 lg:rounded-[0.875rem] lg:bg-card lg:[&>section:first-child>h3]:rounded-t-[0.875rem] lg:[&>section:last-child>div]:rounded-b-[0.875rem]">
    {#each days as d (d.day)}
      <section aria-label={dayLabel(d.day)}>
        <!-- lg:pr-14 keeps the total over the amounts, not over the row's chevron column. -->
        <h3 class="sticky top-[env(safe-area-inset-top)] z-[1] flex items-center justify-between bg-background/85 px-4 py-1.5 text-[13px] font-semibold tracking-[0.14em] text-muted-foreground uppercase backdrop-blur lg:bg-muted lg:py-1 lg:pr-14 lg:text-xs lg:backdrop-blur-none">
          <span>{dayLabel(d.day)}</span>
          {#if Math.abs(d.net) >= 0.005}<span class="tabular-nums normal-case">{fmtSigned(d.net)}</span>{/if}
        </h3>
        <div role="list" class="group-list [--inset:4rem] md:[--inset:5.75rem] lg:rounded-none lg:bg-transparent lg:[--inset:3.75rem]">
          {#each d.rows as { t, i } (t.id)}
            <TxRow {t} {review} {recurring} {family} {oneAccount} {selecting} selected={!!picked[t.id]} onselect={(e, c) => select(e, i, c)}
              onsave={(c) => onsave(t, c)} {onchanged} />
          {/each}
        </div>
      </section>
    {/each}
  </div>
  {#if total > items.length}
    <div bind:this={end} class="mt-3 flex justify-center">
      <Button variant="outline" size="sm" disabled={more || !onmore} onclick={loadMore}>{more ? "Loading…" : `Show more (${total - items.length} left)`}</Button>
    </div>
  {/if}
</div>

{#if pending}
  <ConfirmDialog bind:open={asking} title={pending.title} description={pending.description} confirmLabel={pending.confirm} busyLabel={pending.busy}
    onconfirm={() => send(pending!)} />
{/if}
