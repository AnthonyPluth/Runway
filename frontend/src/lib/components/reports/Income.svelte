<script lang="ts">
  import { api } from "$lib/api";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, monthLabel, plural, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import Bars from "./Bars.svelte";
  import { monthTick, Report } from "./chart.svelte";
  import { monthsOptions, reportState as st } from "./state.svelte";
  import Status from "./Status.svelte";
  import Swatch from "./Swatch.svelte";
  import type { IncomeReport } from "./types";

  // Income vs spending: each month side by side, what was left, and the share of income kept. The server leaves out the
  // months before your first transaction, so the year's count is the months with history. The year isn't over, so a
  // shortfall is "so far" and not flagged red (like Cash flow's month in progress).
  const IN = "var(--cat-3)", OUT = "var(--cat-1)";
  const report = new Report(() => api<IncomeReport>(`/api/reports/income?end=${thisMonth()}&months=${st.months}`));
  const rate = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)}%`);
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Months" value={String(st.months)} options={monthsOptions} onchange={(v) => { st.months = Number(v); report.load(); }} />
</div>

{#if !report.data || report.error}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  {@const y = d.year}
  <StatStrip class="mb-6" items={[
    { label: `Money in, ${y.year}`, value: fmt0(y.income), sub: `${plural(y.months, "month")} so far` },
    { label: `Spent, ${y.year}`, value: fmt0(y.spending), sub: "not card payments or transfers" },
    { label: y.net >= 0 ? "Kept so far" : "Spent more than came in so far", value: fmt0(Math.abs(y.net)),
      sub: y.rate != null ? `savings rate ${rate(y.rate)}` : undefined },
  ]} />

  <Card.Root class="mb-6">
    <Card.Header><Card.Title>Money in and out by month</Card.Title></Card.Header>
    <Card.Content>
      <div class="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span class="inline-flex items-center"><Swatch color={IN} />Money in</span><span class="inline-flex items-center"><Swatch color={OUT} />Spent</span>
      </div>
      <Bars grouped labels={d.months.map((r) => monthTick(r.month, d.months.length > 12 && r.month.endsWith("-01")))}
        series={[{ name: "Money in", color: IN, values: d.months.map((r) => r.income) }, { name: "Spent", color: OUT, values: d.months.map((r) => r.spending) }]}
        label="Money in and Spent by month" tipTitle={(i) => monthLabel(d.months[i].month)}>
        {#snippet extra(i)}
          <div class="flex justify-between gap-3 text-muted-foreground"><span>Left over</span><span class="tabular-nums">{fmt(d.months[i].net)}</span></div>
        {/snippet}
      </Bars>
    </Card.Content>
  </Card.Root>

  <Card.Root>
    <Card.Content>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4">
              <th>Month</th><th class="text-right">Money in</th><th class="text-right">Spent</th><th class="text-right">Left over</th><th class="text-right max-sm:hidden">Savings rate</th>
            </tr>
          </thead>
          <tbody>
            {#each d.months.slice().reverse() as r (r.month)}
              <tr class="border-t [&>td]:py-2.5 [&>td+td]:pl-4">
                <td class="sm:whitespace-nowrap">{monthLabel(r.month)}</td><td class="text-right tabular-nums">{fmt(r.income)}</td>
                <td class="text-right tabular-nums">{fmt(r.spending)}</td>
                <td class={cn("text-right tabular-nums", r.net < 0 && "text-(--low)")}>{r.net < 0 ? "−" : ""}{fmt(Math.abs(r.net))}</td>
                <td class="text-right text-muted-foreground tabular-nums max-sm:hidden">{rate(r.rate)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </Card.Content>
  </Card.Root>
{/if}
