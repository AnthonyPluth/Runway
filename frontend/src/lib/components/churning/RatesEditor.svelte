<script lang="ts">
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { tick } from "svelte";
  import { fromAction } from "svelte/attachments";
  import { isTravel, rowKey, type RateRow } from "./churning";
  import type { Churning } from "./types";
  import { act } from "$lib/act";

  // What a card earns: a base rate for everything else, and a row for each category with its multiplier, marked when it
  // only counts if you book through the issuer's travel portal (then the portal's name; travel categories only). Adding a card keeps these in
  // the form and sends them with it; editing one saves the whole list through `save` whenever a row is complete.
  let { d, base = $bindable(""), rows = $bindable([]), portalName = $bindable(""), save }: {
    d: Pick<Churning, "categories">; base: string | number | null; rows: RateRow[]; portalName: string; save?: () => Promise<void>;
  } = $props();

  const blank = (x: unknown) => x == null || x === "";
  const complete = () => rows.every((r) => r.category && !blank(r.multiplier));
  // Bindings update before this runs (tick), so it saves what's on screen.
  const commit = async () => {
    await tick();
    if (save && complete()) await save();
  };
  const auto = fromAction(autosave, () => commit);
  async function drop(i: number) {
    rows = rows.filter((_, n) => n !== i);
    await act(async () => { await commit(); });
  }
  const anyPortal = $derived(rows.some((r) => r.portal_only));
  const label = (c: { name: string; parent: string | null }) => (c.parent ? `${c.parent} › ${c.name}` : c.name);
  // A rate already marked portal-only keeps its box (whatever its category), so it can be unticked.
  const portalable = (r: RateRow) => !!r.portal_only || isTravel(r.category, d.categories.find((c) => c.name === r.category)?.parent);
</script>

<div class="mt-3" role="group" aria-label="Earning rates">
  <div class="text-sm">Earning rates <span class="text-muted-foreground">(a category's rate covers its subcategories)</span></div>
  <ul class="mt-2 space-y-2">
    <li class="flex flex-wrap items-center gap-2">
      <label class="flex items-center gap-2 text-sm"><span class="w-40">Everywhere else</span>
        <Input type="number" min="0" step="0.25" class="w-20" bind:value={base} aria-label="Points per dollar everywhere else" {@attach save ? auto : undefined} />
        <span class="text-muted-foreground">x</span>
      </label>
    </li>
    {#each rows as r, i (rowKey(r))}
      <li class="flex flex-wrap items-center gap-2">
        <NativeSelect class="w-40" bind:value={r.category} aria-label={`Category of rate ${i + 1}`} {@attach save ? auto : undefined}>
          <option value="">Category…</option>
          {#each d.categories as c (c.name)}<option value={c.name}>{label(c)}</option>{/each}
        </NativeSelect>
        <Input type="number" min="0" step="0.25" class="w-20" bind:value={r.multiplier} aria-label={`Points per dollar on ${r.category || "the category"}`} placeholder="3" {@attach save ? auto : undefined} />
        <span class="text-muted-foreground">x</span>
        {#if portalable(r)}
          <label class="inline-flex items-center gap-2 text-sm"><input type="checkbox" class="size-4" bind:checked={r.portal_only} {@attach save ? auto : undefined} />Only through the issuer's travel portal</label>
        {/if}
        <button type="button" class="cursor-pointer px-1 text-muted-foreground hover:text-foreground" aria-label={`Remove the ${r.category || "new"} rate`} onclick={() => drop(i)}>×</button>
      </li>
    {/each}
  </ul>
  {#if anyPortal}
    <label class="mt-2 flex max-w-sm flex-col gap-1 text-sm">The portal's name
      <Input bind:value={portalName} placeholder="e.g. Capital One Travel" {@attach save ? auto : undefined} />
    </label>
  {/if}
  <Button variant="outline" size="sm" class="mt-2" onclick={() => (rows = [...rows, { category: "", multiplier: "", portal_only: false }])}>Add a rate</Button>
</div>
