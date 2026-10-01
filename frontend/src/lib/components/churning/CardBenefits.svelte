<script lang="ts">
  import { api } from "$lib/api";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { barWidth, fmt0, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { benefitUnuse, benefitUse } from "./actions";
  import BenefitForm from "./BenefitForm.svelte";
  import FoldedLine from "./FoldedLine.svelte";
  import { benefitOrder, benefitState, benefitSummary, canUse, isPerk } from "./churning";
  import type { ChurnCard, Churning } from "./types";

  // A card's benefits (lounge access, travel and Lyft credits, hotel credits): what each is worth a year, this period's
  // remaining amount, and a button to mark it used. The summary nets them against the annual fee. `onchanged` redraws
  // the page's data, so the card comes back with the new figures.
  let { card, d, onchanged }: { card: ChurnCard; d: Churning; onchanged: () => void | Promise<void> } = $props();

  let editing = $state<number | "new" | null>(null);
  let pick = $state("");
  let amounts = $state<Record<number, string>>({});
  let history = $state<Record<number, boolean>>({});
  const presets = $derived(d.benefit_presets.filter((p) => !card.benefits.some((b) => b.preset === p.key)));
  const groups = $derived([...new Set(presets.map((p) => p.group))].map((g) => ({ name: g, items: presets.filter((p) => p.group === g) })));
  const summary = $derived(benefitSummary(card));
  const benefits = $derived([...card.benefits].sort(benefitOrder));
  const kindName = (k: string) => d.benefit_kinds.find((x) => x.key === k)?.name ?? k;
  const periodName = (p: string) => d.benefit_periods.find((x) => x.key === p)?.name ?? p;

  async function addPreset() {
    const key = pick;
    pick = "";
    if (!key) return;
    if (key === "custom") { editing = "new"; return; }
    try {
      const r = await api<{ id: number }>(`/api/churning/cards/${card.id}/benefits`, { method: "POST", body: { preset: key } });
      await onchanged();
      editing = r.id;
      toast("Added. Enter its amount or value.");
    } catch (err) { toast.error((err as Error).message); }
  }
  const closeForm = async (changed: boolean) => { editing = null; if (changed) await onchanged(); };
</script>

<div class="mt-2" role="group" aria-label={`Benefits of ${card.product}`}>
  {#if summary}<p class="mb-2 text-sm text-muted-foreground">{summary}</p>{/if}
  {#if card.benefits.length}
    <ul class="divide-y rounded-lg border bg-background">
      {#each benefits as b (b.id)}
        <li class="p-3">
          {#if editing === b.id}
            {#key b.id}<BenefitForm {card} {d} {b} onclose={closeForm} />{/key}
          {:else}
            <div class="flex flex-wrap items-baseline justify-between gap-x-3">
              <div class="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                {b.name}
                <Badge variant="outline">{kindName(b.kind)}</Badge>
                {#if !isPerk(b)}<span class="text-xs font-normal text-muted-foreground">{periodName(b.period)}</span>{/if}
                {#if !b.counts}<Badge variant="secondary">Not counted</Badge>{/if}
              </div>
              <span class="text-xs text-muted-foreground tabular-nums">{b.value_per_year ? `${fmt0(b.value_per_year)}/yr` : "no value set"}</span>
            </div>
            <div class={cn("mt-1 text-xs", b.expiring ? "font-medium text-[var(--warning)]" : "text-muted-foreground")}>{benefitState(b)}{b.expiring && b.days_left != null ? ` · ${b.days_left} day${b.days_left === 1 ? "" : "s"} left` : ""}</div>
            {#if b.kind === "credit" && b.amount}
              <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={`${b.name} used this period`}
                aria-valuemin={0} aria-valuemax={b.amount} aria-valuenow={b.used ?? 0}>
                <div class="h-full rounded-full bg-[var(--nw-1)]" style:width={barWidth((b.used ?? 0) / b.amount)}></div>
              </div>
            {/if}
            <div class="mt-2 flex flex-wrap items-center gap-2">
              {#if b.kind === "credit" && b.amount && canUse(b)}
                <Input type="number" min="0" step="1" class="h-8 w-28" bind:value={amounts[b.id]} placeholder={`${fmt0(b.remaining)} left`} aria-label={`Amount of ${b.name} used (blank: the rest)`} />
              {/if}
              <Button size="sm" variant="outline" disabled={!canUse(b)} aria-label={`Mark ${b.name} used`}
                onclick={async () => { await benefitUse(b.id, b.name, amounts[b.id], onchanged); amounts[b.id] = ""; }}>Mark used</Button>
              {#if b.used_count > 0}<Button size="sm" variant="ghost" aria-label={`Undo the last use of ${b.name}`} onclick={() => benefitUnuse(b.id, null, onchanged)}>Undo</Button>{/if}
              <Button size="sm" variant="link" class="px-0" aria-label={`Edit ${b.name}`} onclick={() => (editing = b.id)}>Edit</Button>
            </div>
            {#if b.uses.length}
              <FoldedLine class="mt-1 text-xs" count={b.uses.length} noun={b.uses.length === 1 ? "use" : "uses"} bind:open={() => history[b.id] ?? false, (v) => (history[b.id] = v)} />
              {#if history[b.id]}
                <ul class="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  {#each b.uses as u (u.id)}
                    <li class="flex items-center gap-2">
                      <span class="tabular-nums">{fmtDate(u.used_on, { month: "short", day: "numeric", year: "numeric" })}</span>
                      <span>{u.amount_used != null ? fmt0(u.amount_used) : "used"}</span>
                      <Button size="sm" variant="link" class="h-auto px-0 text-xs" aria-label={`Undo the ${fmtDate(u.used_on)} use of ${b.name}`} onclick={() => benefitUnuse(b.id, u.id, onchanged)}>Undo</Button>
                    </li>
                  {/each}
                </ul>
              {/if}
            {/if}
          {/if}
        </li>
      {/each}
    </ul>
  {:else}<p class="text-sm text-muted-foreground">No benefits yet. Add the ones you use to see what the card really costs.</p>{/if}

  {#if editing === "new"}
    {#key "new"}<BenefitForm {card} {d} b={null} onclose={closeForm} />{/key}
  {:else}
    <div class="mt-2">
      <NativeSelect class="max-w-full" bind:value={pick} onchange={addPreset} aria-label={`Add a benefit to ${card.product}`}>
        <option value="">Add a benefit…</option>
        {#each groups as g (g.name)}
          <optgroup label={g.name}>{#each g.items as p (p.key)}<option value={p.key}>{p.name}</option>{/each}</optgroup>
        {/each}
        <option value="custom">Something else…</option>
      </NativeSelect>
    </div>
  {/if}
</div>
