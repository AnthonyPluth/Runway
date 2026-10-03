<script lang="ts">
  import { api } from "$lib/api";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt0, fmtSigned0, monthLabel, monthShort, pct, plural, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import Bars from "./Bars.svelte";
  import { monthTick, Report } from "./chart.svelte";
  import { drill } from "./look";
  import { monthsOptions, reportState as st } from "./state.svelte";
  import Status from "./Status.svelte";
  import Swatch from "./Swatch.svelte";
  import type { IncomeReport } from "./types";

  // Income: money in and money out each month side by side, what was left over, and the savings rate. The tiles are
  // this year so far whatever the months control says (it sets only the chart and its table). The server leaves out
  // the months before your first transaction, so the year's count is the months with history. The year isn't over, so
  // a shortfall is "so far" and not flagged red (like Cash flow's month in progress), and this month is drawn faint.
  const IN = "var(--flow-in)", OUT = "var(--flow-out)";
  const report = new Report(() => api<IncomeReport>(`/api/reports/income?end=${thisMonth()}&months=${st.months}`));
  const rate = (r: number | null) => (r == null ? "—" : pct(r));
  /** The year's tiles in whole dollars that add up: money in and out rounded, left over what's between them. */
  function year(y: IncomeReport["year"]) {
    const i = Math.round(y.income), o = Math.round(y.spending), left = i - o;
    return { i, o, left, rate: i > 0 ? pct(left / i) : null };
  }
</script>

{#if !report.data || report.error}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  {@const y = year(d.year)}
  {@const now = thisMonth()}
  {@const title = (i: number) => `${monthLabel(d.months[i].month)}${d.months[i].month === now ? " (so far)" : ""}`}
  <div aria-busy={report.loading} class={cn("transition-opacity", report.loading && "opacity-60")}>
    <h2 class="eyebrow mb-2">This year</h2>
    <StatStrip class="mb-6" items={[
      { label: "Money in", value: fmt0(y.i), sub: `${plural(d.year.months, "month")} so far` },
      { label: "Money out", value: fmt0(y.o), sub: "not card payments or transfers" },
      { label: "Left over", value: fmtSigned0(y.left).replace(/^\+/, ""), sub: y.rate ? `Savings rate ${y.rate}` : undefined, subClass: "desktop:hidden" },
      ...(y.rate ? [{ label: "Savings rate", value: y.rate, class: "phone:hidden" }] : []),
    ]} />

    <Card.Root class="mb-6">
      <Card.Header class="flex flex-wrap items-center justify-between gap-2">
        <Card.Title>Money in and out by month</Card.Title>
        <Segmented label="Months" value={String(st.months)} options={monthsOptions} onchange={(v) => { st.months = Number(v); report.load(); }} />
      </Card.Header>
      <Card.Content>
        <div class="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span class="inline-flex items-center"><Swatch color={IN} />Money in</span><span class="inline-flex items-center"><Swatch color={OUT} />Money out</span>
        </div>
        <Bars grouped labels={d.months.map((r) => monthTick(r.month, d.months.length > 12 && r.month.endsWith("-01")))}
          partial={d.months.at(-1)?.month === now}
          series={[{ name: "Money in", color: IN, values: d.months.map((r) => r.income) }, { name: "Money out", color: OUT, values: d.months.map((r) => r.spending) }]}
          label="Money in and money out by month" tipTitle={title}
          onpick={(i, name) => showTransactions(drill({ month: d.months[i].month, kind: name === "Money in" ? "in" : name === "Money out" ? "out" : "" }))}
          pickTitle={(i, name) => `See transactions: ${name ? name.toLowerCase() : "everything"}, ${title(i)}`}>
          {#snippet extra(i)}
            <div class="flex justify-between gap-3 text-muted-foreground"><span>Left over</span><span class="tabular-nums">{fmtSigned0(Math.round(d.months[i].income) - Math.round(d.months[i].spending)).replace(/^\+/, "")}</span></div>
          {/snippet}
        </Bars>
      </Card.Content>
    </Card.Root>

    <Card.Root>
      <Card.Content>
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4">
              <th>Month</th><th class="text-right">Money in</th><th class="text-right">Money out</th><th class="text-right">Left over</th><th class="text-right max-sm:hidden">Savings rate</th>
            </tr>
          </thead>
          <tbody>
            {#each d.months.slice().reverse() as r (r.month)}
              {@const left = Math.round(r.income) - Math.round(r.spending)}
              <tr class="border-t [&>td]:py-2.5 [&>td+td]:pl-4 [&>td+td]:whitespace-nowrap">
                <td class="lg:whitespace-nowrap"><span class="max-lg:hidden">{monthLabel(r.month)}</span><span class="lg:hidden">{monthShort(r.month, true)}</span>{#if r.month === now}<span class="text-muted-foreground">{" (so far)"}</span>{/if}</td>
                <td class="text-right tabular-nums">{fmt0(r.income)}</td>
                <td class="text-right tabular-nums">{fmt0(r.spending)}</td>
                <td class={cn("text-right tabular-nums", left < 0 && r.month !== now && "text-(--low)")}>{fmtSigned0(left).replace(/^\+/, "")}</td>
                <td class="text-right text-muted-foreground tabular-nums max-sm:hidden">{rate(r.rate)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </Card.Content>
    </Card.Root>
  </div>
{/if}
