<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt, parseDate } from "$lib/format";
  import type { ForecastEvent } from "$lib/types";
  import { toast } from "svelte-sonner";

  // What's coming up. Click an amount to change just that one occurrence.
  let { events }: { events: ForecastEvent[] } = $props();
  let all = $state(false);
  const shown = $derived(all ? events : events.slice(0, 8));

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

{#if !events.length}
  <p class="py-6 text-center text-sm text-muted-foreground">Nothing scheduled. Add paychecks and bills on the <a class="font-medium text-foreground underline underline-offset-4" href="#recurring">Recurring</a> tab.</p>
{:else}
  <div class="flex flex-col">
    {#each shown as e, i (e.key ?? `${e.date}-${e.name}-${i}`)}
      {@const d = parseDate(e.date)}
      <div class="flex items-center gap-3.5 border-t py-3 first:border-t-0 first:pt-0">
        <div class="flex w-10 shrink-0 flex-col items-center leading-tight">
          <span class="text-xs uppercase text-muted-foreground">{d.toLocaleDateString("en-US", { month: "short" })}</span>
          <b class="text-base font-semibold tabular-nums">{d.getDate()}</b>
        </div>
        <div class="flex min-w-0 flex-1 flex-col gap-0.5">
          <span class="flex flex-wrap items-center gap-1.5 text-sm font-medium">
            {#if e.kind === "recurring"}<span class="text-muted-foreground" title="Recurring item">↻</span>{/if}
            {e.name}
            {#if e.estimated}
              <Badge title={e.kind === "card" ? "Statement hasn't closed yet; based on the card's average over its last 3 statements" : "Based on recent payments"}>estimate</Badge>
            {/if}
            {#if e.overridden}<Badge variant="secondary" title={`Usually ${fmt(e.original_amount)}`}>edited</Badge>{/if}
          </span>
          <span class="text-xs text-muted-foreground tabular-nums">
            {#if e.kind === "card"}Card payment · {:else if e.category || e.kind === "recurring"}{e.category || "Recurring"} · {/if}
            <span class={e.balance_after < 0 ? "font-medium text-destructive" : ""}>balance&nbsp;{fmt(e.balance_after)}</span>
          </span>
        </div>
        <div class="flex shrink-0 flex-col items-end text-sm tabular-nums">
          {#if e.key}
            <AmountEdit amount={e.amount} signed label="Amount" title="Change this amount for this date only" save={(v) => change(e, v)} />
          {:else}{fmt(e.amount)}{/if}
          {#if e.overridden}
            <Button variant="link" size="sm" class="h-auto p-0" title="Go back to the usual amount" onclick={() => reset(e)}>reset</Button>
          {/if}
        </div>
      </div>
    {/each}
  </div>
  {#if events.length > shown.length}
    <Button variant="outline" size="sm" class="mt-3" onclick={() => (all = true)}>Show all {events.length}</Button>
  {/if}
{/if}
