<script lang="ts">
  import { api } from "$lib/api";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { catColor } from "$lib/categories.svelte";
  import { fmt, fmt0, monthLabel, plural, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import X from "@lucide/svelte/icons/x";
  import Bars from "./Bars.svelte";
  import { monthTick, Report } from "./chart.svelte";
  import { monthsOptions, reportState as st } from "./state.svelte";
  import Status from "./Status.svelte";
  import Swatch from "./Swatch.svelte";
  import type { SpendingReport } from "./types";

  // Over time: each month's spending, stacked by category, merchant or account. Click a row to show only that one.
  // Averages are over the full months: the last one is this month, still under way. The server sends only months from the
  // first transaction on, so months before there was any history don't pull them down.
  const GROUPS = { category: "Category", merchant: "Merchant", account: "Account" };
  const report = new Report(() => api<SpendingReport>(`/api/reports/spending?end=${thisMonth()}&months=${st.months}&group=${st.group}`));

  const colored = $derived((report.data?.series ?? []).map((s, i) => ({ ...s, color: catColor(i, s.other) })));
  const focus = $derived(colored.find((s) => s.name === st.focus));
  const shown = $derived(focus ? [focus] : colored);

  const change = (a: number, b: number) => (b > 0 ? (a - b) / b : null);
  const pctTxt = (x: number | null) => (x == null ? "—" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(Math.round(x * 100))}%`);
  const toggle = (name: string) => { st.focus = st.focus === name ? null : name; };
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Group by" value={st.group} options={Object.entries(GROUPS).map(([value, label]) => ({ value, label }))}
    onchange={(v) => { st.group = v as typeof st.group; st.focus = null; report.load(); }} />
  <Segmented label="Months" value={String(st.months)} options={monthsOptions} onchange={(v) => { st.months = Number(v); report.load(); }} />
  {#if focus}
    <span class="inline-flex h-8 items-center gap-1 rounded-full border bg-muted pl-3 pr-1 text-sm">
      {focus.name}
      <button class="grid size-6 cursor-pointer place-items-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground"
        aria-label="Show everything" onclick={() => (st.focus = null)}><X class="size-3.5" /></button>
    </span>
  {/if}
</div>

{#if !report.data || report.error}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  {@const n = d.months.length}
  {@const cur = n - 1}
  {@const lastYear = n >= 13 ? n - 13 : null}
  {@const avg = (values: number[]) => values.slice(0, -1).reduce((a, b) => a + b, 0) / Math.max(1, n - 1)}
  <Card.Root class="mb-6">
    <Card.Header class="flex flex-wrap items-baseline justify-between gap-2">
      <Card.Title>{focus ? focus.name : "Spending"} by month</Card.Title>
      {#if n > 1}
        <span class="text-sm text-muted-foreground" title={`Average of the ${plural(n - 1, "full month")} before this one`}>{fmt0(avg(d.totals))} a month on average</span>
      {/if}
    </Card.Header>
    <Card.Content>
      {#if d.series.length}
        {#if shown.length > 1}
          <div class="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {#each shown as s (s.name)}<span class="inline-flex items-center"><Swatch color={s.color} />{s.name}</span>{/each}
          </div>
        {/if}
        <Bars labels={d.months.map((m) => monthTick(m, n > 12 && m.endsWith("-01")))} series={shown}
          label={`${focus ? focus.name : "Spending"} by month`} tipTitle={(i) => monthLabel(d.months[i])} />
      {:else}
        <p class="py-6 text-center text-sm text-muted-foreground">No spending in these months.</p>
      {/if}
    </Card.Content>
  </Card.Root>

  {#if d.series.length}
    <Card.Root>
      <Card.Content>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4">
                <th>{GROUPS[st.group]}</th><th class="text-right">{monthTick(d.months[cur])}</th>
                {#if n > 1}<th class="text-right">vs {monthTick(d.months[cur - 1])}</th>{/if}
                {#if lastYear != null}<th class="text-right max-sm:hidden">vs {monthTick(d.months[lastYear], true)}</th>{/if}
                {#if n > 1}<th class="text-right max-sm:hidden">Monthly average</th>{/if}<th class="text-right">{n} months</th>
              </tr>
            </thead>
            <tbody>
              {#each colored as s (s.name)}
                {@const c = change(s.values[cur], s.values[cur - 1])}
                {@const on = s.name === st.focus}
                <tr class={cn("relative border-t hover:bg-muted/50 [&>td]:py-2.5 [&>td+td]:pl-4", on && "bg-muted/50")}>
                  <td class="whitespace-nowrap">
                    <button class="cursor-pointer text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                      title={`Show only ${s.name}`} aria-pressed={on} onclick={() => toggle(s.name)}><Swatch color={s.color} />{s.name}</button>
                  </td>
                  <td class="text-right tabular-nums">{fmt(s.values[cur])}</td>
                  {#if n > 1}<td class={cn("text-right tabular-nums", c != null && c > 0.1 ? "text-(--low)" : "text-muted-foreground")}>{pctTxt(c)}</td>{/if}
                  {#if lastYear != null}<td class="text-right text-muted-foreground tabular-nums max-sm:hidden">{pctTxt(change(s.values[cur], s.values[lastYear]))}</td>{/if}
                  {#if n > 1}<td class="text-right text-muted-foreground tabular-nums max-sm:hidden">{fmt(avg(s.values))}</td>{/if}
                  <td class="text-right tabular-nums">{fmt(s.total)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </Card.Content>
    </Card.Root>
  {/if}
{/if}
