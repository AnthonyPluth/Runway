<script lang="ts">
  import { api } from "$lib/api";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { fmt0, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { benefitUnuse, benefitUse } from "./actions";
  import BenefitForm from "./BenefitForm.svelte";
  import { benefitState, benefitSummary, canUse } from "./churning";
  import type { ChurnCard, Churning } from "./types";

  // A card's benefits (lounge access, travel and Lyft credits, hotel credits): what each is worth a year, this period's
  // remaining amount, and a button to mark it used. The summary nets them against the annual fee. `onchanged` redraws
  // the page's data, so the card comes back with the new figures.
  let { card, d, onchanged }: { card: ChurnCard; d: Churning; onchanged: () => void | Promise<void> } = $props();

  let editing = $state<number | "new" | null>(null);
  let pick = $state("");
  let amounts = $state<Record<number, string>>({});
  const presets = $derived(d.benefit_presets.filter((p) => !card.benefits.some((b) => b.preset === p.key)));
  const summary = $derived(benefitSummary(card));
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
  {#if summary}<p class="mb-2 text-sm text-muted-foreground">{summary}{" "}<span class="text-xs">(the annual fee, {fmt0(card.annual_fee)}, less the benefits you'll use)</span></p>{/if}
  {#if card.benefits.length}
    <ul class="divide-y rounded-lg border bg-background">
      {#each card.benefits as b (b.id)}
        <li class="p-3">
          {#if editing === b.id}
            {#key b.id}<BenefitForm {card} {d} {b} onclose={closeForm} />{/key}
          {:else}
            <div class="flex flex-wrap items-baseline justify-between gap-x-3">
              <div class="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                {b.name}
                <Badge variant="outline">{kindName(b.kind)}</Badge>
                <span class="text-xs font-normal text-muted-foreground">{periodName(b.period)}</span>
                {#if !b.counts}<Badge variant="secondary">Not counted</Badge>{/if}
              </div>
              <span class="text-xs text-muted-foreground tabular-nums">{b.value_per_year ? `${fmt0(b.value_per_year)}/yr` : "no value set"}</span>
            </div>
            <div class={cn("mt-1 text-xs", b.expiring ? "font-medium text-[var(--warning)]" : "text-muted-foreground")}>{benefitState(b)}{b.expiring && b.days_left != null ? ` · ${b.days_left} day${b.days_left === 1 ? "" : "s"} left` : ""}</div>
            {#if b.kind === "credit" && b.amount}
              <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={`${b.name} used this period`}
                aria-valuemin={0} aria-valuemax={b.amount} aria-valuenow={b.used ?? 0}>
                <div class="h-full rounded-full bg-[var(--nw-1)]" style:width={`${Math.min(100, ((b.used ?? 0) / b.amount) * 100).toFixed(1)}%`}></div>
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
              <details class="mt-1 text-xs text-muted-foreground">
                <summary class="cursor-pointer">History ({b.uses.length})</summary>
                <ul class="mt-1 space-y-0.5">
                  {#each b.uses as u (u.id)}
                    <li class="flex items-center gap-2">
                      <span class="tabular-nums">{fmtDate(u.used_on, { month: "short", day: "numeric", year: "numeric" })}</span>
                      <span>{u.amount_used != null ? fmt0(u.amount_used) : "used"}</span>
                      <Button size="sm" variant="link" class="h-auto px-0 text-xs" aria-label={`Undo the ${fmtDate(u.used_on)} use of ${b.name}`} onclick={() => benefitUnuse(b.id, u.id, onchanged)}>Undo</Button>
                    </li>
                  {/each}
                </ul>
              </details>
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
        {#each presets as p (p.key)}<option value={p.key}>{p.name}</option>{/each}
        <option value="custom">Something else…</option>
      </NativeSelect>
    </div>
  {/if}
</div>
