<script lang="ts">
  import { commas } from "$lib/commas";
  import { Badge } from "$lib/components/ui/badge";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import type { Churning, DraftBenefit } from "./types";

  // The benefits of a card that isn't saved yet: a row each (name, kind, amount, how often it resets), from the quick-add
  // list or typed in, plus any the AI suggested (marked, to check). They're saved with the card; the rest of a benefit's
  // settings (guests, what it's worth to you, reminders) are on the card's Edit once it exists. `picker` is off when
  // editing a card, whose benefits have their own list: only the AI's pending ones show.
  let { d, rows = $bindable([]), picker = true }: { d: Pick<Churning, "benefit_presets" | "benefit_kinds" | "benefit_periods">; rows: DraftBenefit[]; picker?: boolean } = $props();

  let pick = $state("");
  const used = $derived(new Set(rows.map((r) => r.preset).filter(Boolean)));
  const presets = $derived(d.benefit_presets.filter((p) => !used.has(p.key)));
  const groups = $derived([...new Set(presets.map((p) => p.group))].map((g) => ({ name: g, items: presets.filter((p) => p.group === g) })));
  function add() {
    const key = pick;
    pick = "";
    if (!key) return;
    const p = d.benefit_presets.find((x) => x.key === key);
    rows = [...rows, p ? { name: p.name, kind: p.kind, amount: null, period: p.period, preset: p.key } : { name: "", kind: "credit", amount: null, period: "annual" }];
  }
</script>

<div data-testid="ai-benefits">
  {#if rows.length}
    <ul class="mb-3 space-y-2">
      {#each rows as b, i (i)}
        <li class="flex flex-wrap items-center gap-2">
          <Input class="w-56 max-w-full" bind:value={b.name} aria-label={`${b.ai ? "Suggested benefit" : "Benefit"} ${i + 1}`} placeholder="e.g. Lyft credit" />
          <NativeSelect class="w-28" bind:value={b.kind} aria-label={`Kind of ${b.name || "benefit"}`}>{#each d.benefit_kinds as k (k.key)}<option value={k.key}>{k.name}</option>{/each}</NativeSelect>
          {#if b.kind === "credit"}
            <Input type="number" min="0" step="1" class="w-24" bind:value={b.amount} {@attach commas} placeholder="$" aria-label={`Amount of ${b.name || "benefit"}`} />
          {/if}
          <NativeSelect class="w-32" bind:value={b.period} aria-label={`How often ${b.name || "benefit"} resets`}>{#each d.benefit_periods as p (p.key)}<option value={p.key}>{p.name}</option>{/each}</NativeSelect>
          {#if b.ai}<Badge variant="outline">Suggested by AI, check before saving</Badge>{/if}
          <button type="button" class="cursor-pointer px-1 text-muted-foreground hover:text-foreground" aria-label={`Remove ${b.name || "the benefit"}`} onclick={() => (rows = rows.filter((_, n) => n !== i))}>×</button>
        </li>
      {/each}
    </ul>
  {/if}
  {#if picker}
    <NativeSelect class="max-w-full" bind:value={pick} onchange={add} aria-label="Add a benefit to this card">
      <option value="">Add a benefit…</option>
      {#each groups as g (g.name)}
        <optgroup label={g.name}>{#each g.items as p (p.key)}<option value={p.key}>{p.name}</option>{/each}</optgroup>
      {/each}
      <option value="custom">Something else…</option>
    </NativeSelect>
  {/if}
</div>
