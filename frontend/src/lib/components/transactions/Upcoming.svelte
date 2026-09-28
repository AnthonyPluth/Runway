<script lang="ts" module>
  // "Show all" stays chosen while you move around, as in the classic app.
  let showAll = $state(false);
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import AmountEdit from "$lib/components/AmountEdit.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, fmtDow } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import type { UpcomingEvent } from "./types";

  // Upcoming (projected) items for the forecast account, already filtered like the list below. Click an amount to
  // change just that one occurrence.
  let { events }: { events: UpcomingEvent[] } = $props();
  const shown = $derived(showAll ? events : events.slice(0, 6));

  async function change(e: UpcomingEvent, value: number) {
    try {
      await api("/api/overrides", { method: "POST", body: { key: e.key, amount: (e.amount < 0 ? -1 : 1) * value } });
      toast.success("Updated for this date only");
    } catch (err) { toast.error((err as Error).message); }
    reload();
  }
  async function reset(e: UpcomingEvent) {
    try { await api("/api/overrides", { method: "DELETE", body: { key: e.key } }); toast.success("Back to the usual amount"); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

{#if events.length}
  <Card.Root class="mb-6 gap-3 border-dashed">
    <Card.Header><Card.Title class="flex items-center gap-2">Upcoming <Badge variant="secondary">projected</Badge></Card.Title></Card.Header>
    <Card.Content class="px-0">
      <div class="overflow-x-auto px-6">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs text-muted-foreground">
              <th class="py-2 pr-2 font-medium">Date</th><th class="px-2 py-2 font-medium">Item</th>
              <th class="px-2 py-2 font-medium max-sm:hidden">Account</th><th class="px-2 py-2 text-right font-medium">Amount</th>
              <th class="px-2 py-2 font-medium">Category</th><th class="py-2 pl-2 text-right font-medium">Balance after</th>
            </tr>
          </thead>
          <tbody>
            {#each shown as e, i (e.key ?? `${e.date}-${e.name}-${i}`)}
              <tr class="border-t">
                <td class="whitespace-nowrap py-2 pr-2 text-muted-foreground">{fmtDow(e.date)}</td>
                <td class="px-2 py-2">
                  <span class="flex flex-wrap items-center gap-1.5">
                    <span class="whitespace-nowrap">{#if e.kind === "recurring"}<span class="mr-1.5 text-muted-foreground" title="Recurring item">↻</span>{/if}{e.name}</span>
                    {#if e.estimated}<Badge variant="secondary">estimate</Badge>{/if}
                    {#if e.late_from}<Badge variant="secondary" title={`Was due ${e.late_from} and hasn't shown up yet`}>late</Badge>{/if}
                    {#if e.overridden}<Badge class="bg-primary/15 text-primary" title={`Usually ${fmt(e.original_amount)}`}>edited</Badge>{/if}
                  </span>
                </td>
                <td class="px-2 py-2 text-muted-foreground max-sm:hidden">{#if e.account_id}<AcctLabel id={e.account_id} name={e.account ?? ""} />{/if}</td>
                <td class="whitespace-nowrap px-2 py-2 text-right">
                  {#if e.key}
                    <AmountEdit amount={e.amount} signed label="Amount" title="Change this amount for this date only" save={(v) => change(e, v)} />
                  {:else}{fmt(e.amount)}{/if}
                  {#if e.overridden}
                    <Button variant="link" size="sm" class="h-auto px-1" title="Go back to the usual amount" onclick={() => reset(e)}>reset</Button>
                  {/if}
                </td>
                <td class="px-2 py-2 text-muted-foreground">{e.category || "—"}</td>
                <td class={cn("whitespace-nowrap py-2 pl-2 text-right tabular-nums", e.balance_after < 0 ? "font-medium text-destructive" : "text-muted-foreground")}>
                  {e.balance_after < 0 ? "▲ " : ""}{fmt(e.balance_after)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if events.length > shown.length}
        <Button variant="link" size="sm" class="mx-6 mt-1 px-0" onclick={() => (showAll = true)}>Show all {events.length} upcoming</Button>
      {/if}
    </Card.Content>
  </Card.Root>
{/if}
