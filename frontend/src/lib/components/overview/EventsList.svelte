<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDow } from "$lib/format";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { ForecastEvent } from "$lib/types";
  import { toast } from "svelte-sonner";

  // What's coming up. Click an amount to change just that one occurrence. `limit` is how many show before
  // "Show all"; `accounts` adds each one's account (Transactions shows several accounts' items together).
  let { events, limit = 8, accounts = false, all = $bindable(false) }: {
    events: (ForecastEvent & { late_from?: string | null })[];
    limit?: number; accounts?: boolean; all?: boolean;
  } = $props();
  const shown = $derived(all ? events : events.slice(0, limit));

  async function change(e: ForecastEvent, value: number) {
    try {
      await api("/api/overrides", { method: "POST", body: { key: e.key, amount: (e.amount < 0 ? -1 : 1) * value } });
      toast.success("Updated for this date only");
      reload();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function reset(e: ForecastEvent) {
    try { await api("/api/overrides", { method: "DELETE", body: { key: e.key } }); toast.success("Back to the usual amount"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<!-- Rows of a grouped list (the caller puts them in a Group). -->
{#if !events.length}
  <p class="cell text-sm text-muted-foreground">Nothing scheduled. Add paychecks and bills on&nbsp;<a class="font-medium text-primary" href="#recurring">Recurring</a>.</p>
{:else}
  {#each shown as e, i (e.key ?? `${e.date}-${e.name}-${i}`)}
    {@const bank = e.kind === "card" && e.card_id ? app.state?.brands?.[e.card_id] : undefined}
    <div class="cell">
      <!-- Logos as they are, with nothing behind them, as in Transactions. -->
      {#if e.logo}
        <img class="size-8 shrink-0 rounded-lg object-contain" src={e.logo} alt="" loading="lazy" width="32" height="32" />
      {:else if bank?.logo}
        <img class="size-8 shrink-0 rounded-lg object-contain" src={`/banks/${bank.logo}.svg`} alt="" title={bank.institution ?? ""} loading="lazy" width="32" height="32" />
      {:else}
        <CatIcon name={e.kind === "card" ? "Credit Card Payment" : e.category} size={32} solid />
      {/if}
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-1.5 text-[15px]">
          <span class="truncate">{e.name}</span>
          {#if e.kind === "recurring"}<span class="text-xs text-muted-foreground" title="Recurring item" aria-label="Recurring item">↻</span>{/if}
          {#if e.estimated}
            <Badge variant="secondary" title={e.kind === "card" ? "Statement hasn't closed yet; based on the card's average over its last 3 statements" : "Based on recent payments"}>estimate</Badge>
          {/if}
          {#if e.late_from}<Badge variant="secondary" title={`Was due ${e.late_from} and hasn't shown up yet`}>late</Badge>{/if}
          {#if e.overridden}<Badge variant="secondary" title={`Usually ${fmt(e.original_amount)}`}>edited</Badge>{/if}
        </div>
        <div class="truncate text-[13px] text-muted-foreground tabular-nums">
          {fmtDow(e.date)}{#if accounts && e.account}{" · "}{e.account}{/if} ·
          <span class={e.balance_after < 0 ? "font-medium text-destructive" : ""}>balance {fmt(e.balance_after)}</span>
        </div>
      </div>
      <div class={["flex shrink-0 flex-col items-end text-[15px] tabular-nums", e.amount > 0 && "text-emerald-400"]}>
        {#if e.key}
          <AmountEdit amount={e.amount} signed label="Amount" title="Change this amount for this date only" save={(v) => change(e, v)} />
        {:else}{fmt(e.amount)}{/if}
        {#if e.overridden}
          <Button variant="link" size="sm" class="h-auto p-0 text-xs" title="Go back to the usual amount" onclick={() => reset(e)}>reset</Button>
        {/if}
      </div>
    </div>
  {/each}
  {#if events.length > shown.length}
    <button type="button" class="cell justify-between text-[15px] text-primary" onclick={() => (all = true)}>
      Show all {events.length}<ChevronRight class="size-4 text-muted-foreground" aria-hidden="true" />
    </button>
  {/if}
{/if}
