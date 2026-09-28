<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import * as Card from "$lib/components/ui/card";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { fmt, plural } from "$lib/format";
  import { toast } from "svelte-sonner";
  import TxRow from "./TxRow.svelte";
  import type { RecurringItem, Tx } from "./types";

  // The list, with checkboxes (shift-click for a range) to change many transactions together: a category, the
  // merchant's name, or marking them reviewed. The bar for that sticks to the top while you scroll.
  let { items, total, review, recurring, onsave, onchanged }: {
    items: Tx[]; total: number; review: boolean; recurring: RecurringItem[];
    onsave: (t: Tx, category: string) => Promise<void>; onchanged: () => void;
  } = $props();

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
      refreshState(); onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
  function doRename() { const v = rename.trim(); if (v) send({ payee: v }, `Renamed to ${v}`); }
</script>

<div>
  {#if ids.length}
    <div class="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-2.5 rounded-lg border bg-popover p-2.5 text-sm shadow-md" role="region" aria-label="Change the selected transactions">
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

  <Card.Root class="gap-0 overflow-hidden py-0">
    <div class="overflow-x-auto">
      <table class="w-full text-sm" data-tx-list>
        <thead>
          <tr class="text-left text-xs text-muted-foreground">
            <th class="w-8 py-2.5 pl-3 pr-1 font-medium">
              <input type="checkbox" class="size-4 cursor-pointer accent-primary align-middle" aria-label="Select all shown"
                checked={ids.length > 0 && ids.length === items.length} indeterminate={ids.length > 0 && ids.length < items.length}
                onchange={(e) => all(e.currentTarget.checked)} />
            </th>
            <th class="px-2 py-2.5 font-medium">Date</th>
            <th class="px-2 py-2.5 font-medium">Merchant</th>
            <th class="px-2 py-2.5 font-medium max-sm:hidden">Account</th>
            <th class="px-2 py-2.5 text-right font-medium">Amount</th>
            <th class="px-2 py-2.5 pr-3 font-medium">Category</th>
          </tr>
        </thead>
        <tbody>
          {#each items as t, i (t.id)}
            <TxRow {t} {review} {recurring} selected={!!picked[t.id]} onselect={(e, c) => select(e, i, c)}
              onsave={(c) => onsave(t, c)} {onchanged} />
          {/each}
        </tbody>
      </table>
    </div>
  </Card.Root>
  {#if total > items.length}
    <p class="mt-2 text-sm text-muted-foreground">Showing {items.length} of {total}. Narrow the search to see more.</p>
  {/if}
</div>
