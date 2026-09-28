<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt, fmtDate, monthLabel, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import Bars from "./Bars.svelte";
  import { monthTick } from "./chart.svelte";
  import type { MerchantReport } from "./types";

  // One merchant opened in the Merchants table: its last 12 months and latest transactions.
  let { name }: { name: string } = $props();
  const data = $derived(api<MerchantReport>(`/api/reports/merchant?name=${encodeURIComponent(name)}&end=${thisMonth()}&months=12`));
</script>

<div class="rounded-lg border bg-muted/30 p-3">
  {#await data}
    <p class="text-sm text-muted-foreground">Loading…</p>
  {:then m}
    <div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <b class="font-semibold">{m.name}</b>
      <span class="text-sm text-muted-foreground">{fmt(m.total)} in the last 12 months</span>
      <Button variant="link" class="ml-auto h-auto p-0" onclick={() => showTransactions({ q: m.name })}>All its transactions</Button>
    </div>
    <div class="mt-1 mb-2">
      <Bars labels={m.months.map((x) => monthTick(x))} series={[{ name: m.name, values: m.values, color: "var(--cat-1)" }]} height={160}
        tipTitle={(i) => monthLabel(m.months[i])} label={`Spending at ${m.name} by month`} />
    </div>
    <table class="w-full text-xs">
      <tbody>
        {#each m.transactions.slice(0, 12) as t (t.id)}
          <tr class="border-t [&>td]:py-1.5 [&>td]:pr-2">
            <td class="whitespace-nowrap text-muted-foreground">{fmtDate(t.posted)}</td><td class="text-muted-foreground">{t.account_name}</td>
            <td class="text-muted-foreground">{t.category || "—"}</td>
            <td class={cn("text-right tabular-nums", t.amount > 0 && "text-(--good)")}>{fmt(t.amount)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:catch err}
    <p class="text-sm">Something went wrong: {err.message}</p>
  {/await}
</div>
