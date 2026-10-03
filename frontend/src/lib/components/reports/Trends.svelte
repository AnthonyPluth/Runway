<script lang="ts">
  import { api } from "$lib/api";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { catColor } from "$lib/categories.svelte";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt0, monthLabel, pctSigned, plural, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Focus from "@lucide/svelte/icons/focus";
  import X from "@lucide/svelte/icons/x";
  import Bars from "./Bars.svelte";
  import { monthTick, Report } from "./chart.svelte";
  import { catFill, catFilter, drill } from "./look";
  import { monthsOptions, reportState as st } from "./state.svelte";
  import Status from "./Status.svelte";
  import Swatch from "./Swatch.svelte";
  import type { SpendingReport } from "./types";

  // Over time: each month's spending, stacked by category, merchant or account. A row's focus button shows only that
  // one. Averages are over the full months: the last one is this month, still under way, so it's drawn faint and
  // compared with last month (and a year ago) up to the same day, not with the whole of it. The server sends only
  // months from the first transaction on, so months before there was any history don't pull the averages down.
  // Categories wear their own colors (as on Budget); merchants and accounts take the chart colors by rank.
  const GROUPS = { category: "Category", merchant: "Merchant", account: "Account" };
  const report = new Report(() => api<SpendingReport>(`/api/reports/spending?end=${thisMonth()}&months=${st.months}&group=${st.group}`));

  type Row = SpendingReport["series"][number] & { color: string };
  const colored = $derived<Row[]>((report.data?.series ?? []).map((s, i) => ({
    ...s, color: report.data?.group === "category" ? catFill(s.name, s.other) : catColor(i, s.other) })));
  const focus = $derived(colored.find((s) => s.name === st.focus));
  const shown = $derived(focus ? [focus] : colored);
  let others = $state(false);   // "Everything else" opened to list what's in it

  /** A change against a base: a share, "new" when there was nothing to compare with, or null when both are nothing. */
  const change = (a: number, b: number | null | undefined): number | "new" | null =>
    b == null ? null : b > 0 ? (a - b) / b : a > 0 ? "new" : null;
  const changeTxt = (c: ReturnType<typeof change>) => (c === "new" ? "New" : c == null ? "—" : pctSigned(c));
  const toggle = (name: string) => { st.focus = st.focus === name ? null : name; };

  // Transactions behind a column (or one series in it): the month, and the category, merchant or account.
  function filtersFor(month: string, name: string | null) {
    const s = name ? colored.find((x) => x.name === name) : focus;
    if (!s || s.other) return drill({ month, kind: "out" });
    if (report.data?.group === "merchant") return drill({ month, q: s.name, kind: "out" });
    if (report.data?.group === "account") return drill({ month, account: s.account ?? "", kind: "out" });
    return drill({ month, category: catFilter(s.name) });
  }
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Group by" value={st.group} options={Object.entries(GROUPS).map(([value, label]) => ({ value, label }))}
    onchange={(v) => { st.group = v as typeof st.group; st.focus = null; report.load(); }} />
  <Segmented label="Months" value={String(st.months)} options={monthsOptions} onchange={(v) => { st.months = Number(v); report.load(); }} />
  {#if focus}
    <span class="inline-flex h-8 max-w-full items-center gap-1 rounded-full border bg-muted pl-3 pr-1 text-sm">
      <span class="truncate" title={focus.name}>{focus.name}</span>
      <button class="grid size-6 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground phone:size-11 phone:-my-2"
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
  {@const partial = !!d.through}
  {@const day = d.through ? Number(d.through.slice(8, 10)) : 0}
  {@const lastYear = n >= 13 ? n - 13 : null}
  {@const sums = d.months.map((_, i) => shown.reduce((a, s) => a + s.values[i], 0))}
  {@const avg = (values: number[]) => values.slice(0, -1).reduce((a, b) => a + b, 0) / Math.max(1, n - 1)}
  {@const curLabel = `${monthTick(d.months[cur])}${partial ? " (so far)" : ""}`}
  {@const vsLabel = (m: string) => `vs ${monthTick(m, m.slice(0, 4) !== d.months[cur].slice(0, 4))}${partial ? ` 1${day > 1 ? `–${day}` : ""}` : ""}`}
  {@const title = (i: number) => `${monthLabel(d.months[i])}${partial && i === cur ? " (so far)" : ""}`}
  <div aria-busy={report.loading} class={cn("transition-opacity", report.loading && "opacity-60")}>
    <Card.Root class="mb-6">
      <Card.Header class="flex flex-wrap items-baseline justify-between gap-2">
        <Card.Title class="min-w-0 truncate">{focus ? focus.name : "Spending"} by month</Card.Title>
        {#if n > 1}
          <span class="text-sm text-muted-foreground" title={`Average of the ${plural(n - 1, "full month")} before this one`}>{fmt0(avg(sums))} a month on average</span>
        {/if}
      </Card.Header>
      <Card.Content>
        {#if d.series.length}
          {#if shown.length > 1}
            <div class="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {#each shown as s (s.name)}<span class="inline-flex max-w-full items-center"><Swatch color={s.color} /><span class="truncate">{s.name}</span></span>{/each}
            </div>
          {/if}
          <Bars labels={d.months.map((m) => monthTick(m, n > 12 && m.endsWith("-01")))} series={shown} {partial}
            avg={n > 1 ? avg(sums) : null}
            label={`${focus ? focus.name : "Spending"} by month`} tipTitle={title}
            onpick={(i, name) => showTransactions(filtersFor(d.months[i], name))}
            pickTitle={(i, name) => `See transactions: ${name ?? focus?.name ?? "spending"}, ${title(i)}`} />
          <details class="group mt-2">
            <summary class="flex w-fit cursor-pointer list-none items-center gap-1.5 text-sm text-muted-foreground select-none hover:text-foreground phone:min-h-11 [&::-webkit-details-marker]:hidden">
              <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Show as table
            </summary>
            <div class="overflow-x-auto">
              <table class="mt-2 w-full text-sm" aria-label={`${focus ? focus.name : "Spending"} by month`}>
                <thead>
                  <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4">
                    <th class="sticky left-0 bg-card">Month</th>
                    {#each shown as s (s.name)}<th class="max-w-32 truncate text-right" title={s.name}>{s.name}</th>{/each}
                    {#if shown.length > 1}<th class="text-right">Total</th>{/if}
                  </tr>
                </thead>
                <tbody>
                  {#each d.months.map((m, i) => ({ m, i })).reverse() as r (r.m)}
                    <tr class="border-t [&>td]:py-1.5 [&>td+td]:pl-4">
                      <td class="sticky left-0 bg-card whitespace-nowrap">{title(r.i)}</td>
                      {#each shown as s (s.name)}<td class="text-right tabular-nums">{fmt0(s.values[r.i])}</td>{/each}
                      {#if shown.length > 1}<td class="text-right font-medium tabular-nums">{fmt0(sums[r.i])}</td>{/if}
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </details>
        {:else}
          <p class="py-6 text-center text-sm text-muted-foreground">No spending in these months.</p>
        {/if}
      </Card.Content>
    </Card.Root>

    {#if d.series.length}
      <Card.Root>
        <Card.Content>
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4 [&>th+th]:whitespace-nowrap">
                <th>{GROUPS[d.group]}</th><th class="text-right whitespace-nowrap">{curLabel}</th>
                {#if n > 1}<th class="text-right max-[480px]:hidden">{vsLabel(d.months[cur - 1])}</th>{/if}
                {#if lastYear != null}<th class="text-right max-lg:hidden">{vsLabel(d.months[lastYear])}</th>{/if}
                {#if n > 1}<th class="text-right max-lg:hidden">Monthly average</th>{/if}<th class="text-right">{n} months</th>
              </tr>
            </thead>
            <tbody>
              {#each colored as s (s.name)}
                {@const prev = partial ? s.same_point?.prev : s.values[cur - 1]}
                {@const ago = lastYear == null ? null : partial ? s.same_point?.year_ago : s.values[lastYear]}
                {@const c = change(s.values[cur], prev)}
                {@const on = s.name === st.focus}
                <tr class={cn("relative border-t hover:bg-muted/50 [&>td]:py-2.5 [&>td+td]:pl-4 [&>td+td]:whitespace-nowrap", on && "bg-muted/50")}>
                  <td class="w-full max-w-0 min-w-28">
                    {#if s.other}
                      <button class="flex max-w-full cursor-pointer items-center text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                        aria-expanded={others} onclick={() => (others = !others)}>
                        <Swatch color={s.color} /><span class="truncate">{s.name}</span>
                        <ChevronRight class={cn("ml-1 size-3.5 shrink-0 text-muted-foreground transition-transform", others && "rotate-90")} aria-hidden="true" />
                      </button>
                    {:else}
                      <button class="flex max-w-full cursor-pointer items-center text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                        title={s.name} aria-label={`${s.name}: show only this`} aria-pressed={on} onclick={() => toggle(s.name)}>
                        <Swatch color={s.color} /><span class="truncate">{s.name}</span>
                        <Focus class={cn("ml-1.5 size-3.5 shrink-0", on ? "text-foreground" : "text-muted-foreground/70")} aria-hidden="true" />
                      </button>
                    {/if}
                  </td>
                  <td class="text-right tabular-nums">{fmt0(s.values[cur])}</td>
                  {#if n > 1}<td class={cn("text-right tabular-nums max-[480px]:hidden", typeof c === "number" && c > 0.1 ? "text-(--low)" : "text-muted-foreground")}
                    title={partial ? `${fmt0(prev)} by the same day` : undefined}>{changeTxt(c)}</td>{/if}
                  {#if lastYear != null}<td class="text-right text-muted-foreground tabular-nums max-lg:hidden">{changeTxt(change(s.values[cur], ago))}</td>{/if}
                  {#if n > 1}<td class="text-right text-muted-foreground tabular-nums max-lg:hidden">{fmt0(avg(s.values))}</td>{/if}
                  <td class="text-right tabular-nums">{fmt0(s.total)}</td>
                </tr>
                {#if s.other && others && s.members?.length}
                  <tr><td colspan="6" class="pb-2.5 pl-[17px] text-xs text-muted-foreground">{s.members.join(" · ")}</td></tr>
                {/if}
              {/each}
            </tbody>
          </table>
        </Card.Content>
      </Card.Root>
    {/if}
  </div>
{/if}
