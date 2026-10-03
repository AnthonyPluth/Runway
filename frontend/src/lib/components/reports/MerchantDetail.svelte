<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt, fmt0, fmtDate, monthLabel, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import Bars from "./Bars.svelte";
  import { monthTick, Report } from "./chart.svelte";
  import { drill } from "./look";
  import Status from "./Status.svelte";
  import type { MerchantReport } from "./types";

  // One merchant opened in the Merchants table: its last 12 months and latest transactions. "All its transactions"
  // opens Transactions on it for the period the table shows; a month's column, or a row, for that month or day.
  let { name, period }: { name: string; period: { from: string; to: string } } = $props();
  const now = thisMonth();
  const report = new Report(() => api<MerchantReport>(`/api/reports/merchant?name=${encodeURIComponent(name)}&end=${now}&months=12`));
  const title = (m: string) => `${monthLabel(m)}${m === now ? " (so far)" : ""}`;
</script>

<div class="rounded-lg border bg-muted/30 p-3">
  {#if report.error}
    <Status compact error={report.error} retry={() => report.load()} what={name} />
  {:else if !report.data}
    <p class="text-sm text-muted-foreground" role="status">Loading…</p>
  {:else}
    {@const m = report.data}
    <div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <b class="min-w-0 truncate font-semibold" title={m.name}>{m.name}</b>
      <span class="text-sm text-muted-foreground">{fmt0(m.total)} in the last 12 months</span>
      <Button variant="link" class="ml-auto h-auto p-0 phone:min-h-11" onclick={() => showTransactions(drill({ q: name, ...period }))}>All its transactions</Button>
    </div>
    <div class="mt-1 mb-2">
      <Bars labels={m.months.map((x) => monthTick(x))} series={[{ name: m.name, values: m.values, color: "var(--flow-out)" }]} height={160}
        partial={m.months.at(-1) === now} tipTitle={(i) => title(m.months[i])} label={`Spending at ${m.name} by month`}
        onpick={(i) => showTransactions(drill({ q: name, month: m.months[i] }))} pickTitle={(i) => `See transactions: ${title(m.months[i])}`} />
    </div>
    <table class="w-full text-xs">
      <tbody>
        {#each m.transactions.slice(0, 12) as t (t.id)}
          <tr class="relative border-t hover:bg-muted/50 [&>td]:py-1.5 [&>td]:pr-2">
            <td class="whitespace-nowrap text-muted-foreground">
              <button class="cursor-pointer text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                title="See it in Transactions" onclick={() => showTransactions(drill({ q: name, from: t.posted, to: t.posted }))}>{fmtDate(t.posted)}</button>
            </td>
            <td class="max-w-0 truncate text-muted-foreground max-md:hidden" title={t.account_name}>{t.account_name}</td>
            <td class="max-w-0 truncate text-muted-foreground max-md:hidden" title={t.category || undefined}>{t.category || "—"}</td>
            <td class={cn("text-right whitespace-nowrap tabular-nums", t.amount > 0 && "text-(--flow-in)")}>{fmt(t.amount)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
