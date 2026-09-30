<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { fmt, fmtDate, plural } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import TxRow from "./TxRow.svelte";
  import type { RecurringItem, Tx } from "./types";

  // The list, a day at a time, with checkboxes (shift-click for a range) to change many transactions together: a
  // category, the merchant's name, or marking them reviewed. The bar for that sticks to the top while you scroll.
  // More load as you reach the bottom (`onmore`). On a phone the checkboxes show once you tap Select.
  let { items, total, review, recurring, selecting = $bindable(false), onsave, onchanged, onmore }: {
    items: Tx[]; total: number; review: boolean; recurring: RecurringItem[]; selecting?: boolean;
    onsave: (t: Tx, category: string) => Promise<void>; onchanged: () => void; onmore?: () => Promise<void>;
  } = $props();

  // Days, newest first, each with what came in and went out that day.
  const days = $derived.by(() => {
    const out: { day: string; rows: { t: Tx; i: number }[]; net: number }[] = [];
    items.forEach((t, i) => {
      const day = t.posted.slice(0, 10);
      if (out.at(-1)?.day !== day) out.push({ day, rows: [], net: 0 });
      const d = out.at(-1)!;
      d.rows.push({ t, i }); d.net += t.amount;
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
  const sum = $derived(items.reduce((n, t) => n + (picked[t.id] ? t.amount : 0), 0));
  let bulkCat = $state("");
  let rename = $state("");

  function select(e: MouseEvent, i: number, checked: boolean) {
    if (e.shiftKey && last !== null) {   // shift-click: everything between the last tick and this one
      for (let k = Math.min(last, i); k <= Math.max(last, i); k++) picked[items[k].id] = checked;
    } else picked[items[i].id] = checked;
    last = i;
  }
  function all(checked: boolean) { for (const t of items) picked[t.id] = checked; }

  async function send(body: Record<string, unknown>, what: string) {
    try {
      const r = await api<{ updated: number }>("/api/transactions/bulk", { method: "POST", body: { ids, ...body } });
      toast.success(`${what} · ${plural(r.updated, "transaction")}`);
      picked = {};   // done with these; the list below updates where it is
      refreshState(); onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
  function doRename() { const v = rename.trim(); if (v) send({ payee: v }, `Renamed to ${v}`); }
</script>

<div>
  {#if ids.length}
    <div class="sticky top-[env(safe-area-inset-top)] z-10 mb-3 flex flex-wrap items-center gap-2.5 rounded-lg border bg-popover p-2.5 text-sm shadow-md" role="region" aria-label="Change the selected transactions">
      <span class="tabular-nums"><b>{ids.length} selected</b> <span class="text-muted-foreground">{fmt(sum)}</span></span>
      <CategorySelect bind:value={bulkCat} blank="Set category…" label="Category for the selected transactions" class="w-48"
        onchange={(v) => v && send({ category: v }, `Set to ${v}`)} />
      <span class="flex items-center gap-1.5">
        <Input bind:value={rename} placeholder="Rename merchant to…" aria-label="New merchant name" class="w-48"
          onkeydown={(e) => { if (e.key === "Enter") doRename(); }} />
        <Button variant="outline" size="sm" onclick={doRename}>Rename</Button>
      </span>
      <Button variant="outline" size="sm" title="Keep their categories and take them out of Review"
        onclick={() => send({ reviewed: true }, "Marked reviewed")}>Mark reviewed</Button>
      <Button variant="link" size="sm" onclick={() => all(false)}>Clear</Button>
    </div>
  {/if}

  <div class="mb-2 flex items-center gap-3 px-4 text-[13px] text-muted-foreground">
    <label class={cn("flex items-center", !selecting && "max-md:hidden")}>
      <input type="checkbox" class="size-4 cursor-pointer accent-primary" aria-label="Select all shown"
        checked={ids.length > 0 && ids.length === items.length} indeterminate={ids.length > 0 && ids.length < items.length}
        onchange={(e) => all(e.currentTarget.checked)} />
    </label>
    <span class="tabular-nums">{items.length < total ? `${items.length} of ${total}` : plural(total, "transaction")}</span>
    <button type="button" class="ml-auto cursor-pointer text-[15px] text-primary md:hidden"
      onclick={() => { selecting = !selecting; if (!selecting) all(false); }}>{selecting ? "Done" : "Select"}</button>
  </div>
  <!-- A grouped list per day, its heading staying in view while you scroll through that day. -->
  <div data-tx-list class="flex flex-col gap-4">
    {#each days as d (d.day)}
      <section aria-label={dayLabel(d.day)}>
        <h3 class="sticky top-[env(safe-area-inset-top)] z-[1] flex items-center justify-between bg-background/85 px-4 py-1.5 text-[13px] font-medium tracking-wide text-muted-foreground uppercase backdrop-blur">
          <span>{dayLabel(d.day)}</span>
          {#if Math.abs(d.net) >= 0.005}<span class="tabular-nums normal-case">{fmt(d.net)}</span>{/if}
        </h3>
        <div role="list" class="group-list [--inset:4rem] md:[--inset:5.75rem]">
          {#each d.rows as { t, i } (t.id)}
            <TxRow {t} {review} {recurring} {selecting} selected={!!picked[t.id]} onselect={(e, c) => select(e, i, c)}
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
