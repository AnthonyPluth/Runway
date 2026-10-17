<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { fmt } from "$lib/format";
  import { commas } from "$lib/commas";
  import { onMount } from "svelte";
  import { qty } from "./numbers";
  import type { Holding } from "./types";

  // The price paid per share for a holding, one input per account holding it (the institution's number is the default),
  // saved as you leave each one. Shared by the Holdings table and the holding's page. `ondone` hears whether anything was
  // saved when it closes (with Done, or on its own once a holding in one account is saved).
  let { holding: h, ondone }: { holding: Holding; ondone: (changed: boolean) => void } = $props();

  let changed = false;
  let root = $state<HTMLDivElement | null>(null);
  let typed = $state<Record<number, string>>(Object.fromEntries(h.lots.map((l, i) => [i, l.manual && l.per_share != null ? String(+l.per_share.toFixed(4)) : ""])));
  onMount(() => root?.querySelector<HTMLInputElement>(".cb-in")?.focus());
  const saveLot = (i: number) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    const l = h.lots[i];
    await api("/api/investments/cost", { method: "POST", body: { account_id: l.account_id, security_id: l.security_id || h.security_id, per_share: f.value } });
    changed = true;
    if (h.lots.length === 1) ondone(true);   // one account: done as soon as it's saved
  };
</script>

<div bind:this={root}>
  <p class="text-sm" title="Your average if you bought at different prices. Runway multiplies it by the shares you hold."><b>Price paid per share for {h.ticker || h.name || ""}</b></p>
  <p class="text-xs text-muted-foreground">Leave it empty to go back to the institution’s figure</p>
  {#each h.lots as l, i (l.account_id)}
    <div class="mt-3 flex flex-wrap items-end gap-3">
      <label class="flex flex-col gap-1 text-sm">{l.account_name} · {qty(Number(l.quantity))} shares
        <span class="relative inline-block">
          <span class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true">$</span>
          <input type="number" min="0" step="0.0001" {@attach commas} class="cb-in h-9 w-40 rounded-md border border-input bg-transparent pr-2 pl-6 text-sm dark:bg-input/30"
            value={typed[i]} oninput={(e) => (typed[i] = e.currentTarget.value)} use:autosave={saveLot(i)} aria-label={`Price per share in ${l.account_name}`}
            placeholder={l.reported_cost_basis && l.quantity ? `reported ${(l.reported_cost_basis / l.quantity).toFixed(2)}` : "not reported"} />
        </span>
      </label>
      <span class="pb-2 text-sm text-muted-foreground">{typed[i] && l.quantity ? `= ${fmt(Number(typed[i]) * l.quantity)} cost basis` : ""}</span>
    </div>
  {/each}
  <Button variant="link" size="sm" class="mt-2 px-0" onclick={() => ondone(changed)}>Done</Button>
</div>
