<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt, fmt0, fmtDate, pct } from "$lib/format";
  import { isPhone } from "$lib/phone.svelte";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { Report } from "./chart.svelte";
  import { catFill, catFilter, dayBefore, drill } from "./look";
  import { rangeDates, rangeOptions, reportState as st, type RangeKey } from "./state.svelte";
  import Status from "./Status.svelte";
  import Swatch from "./Swatch.svelte";
  import Treemap from "./Treemap.svelte";
  import type { BreakdownNode, BreakdownReport, ReportTx } from "./types";

  // Breakdown: a treemap you click into: categories -> subcategories -> merchants -> transactions, each of which opens
  // in Transactions. Categories wear their own colors (as on Budget and in Cash flow).
  const report = new Report(async () => {
    const { start, end } = rangeDates(st.range);
    const d = await api<BreakdownReport>(`/api/reports/breakdown?start=${start}&end=${end}`);
    st.path = walk(d.tree, st.path).map((t) => t.name);   // forget clicks that don't exist in this period
    return d;
  });

  // Follow the clicked names down the tree. A level with only one thing in it (a category with one merchant)
  // opens straight onto that one.
  function walk(tree: BreakdownNode, path: string[]): BreakdownNode[] {
    let node = tree;
    const trail: BreakdownNode[] = [];
    for (const name of path) {
      const next = (node.children || []).find((c) => c.name === name);
      if (!next) break;
      trail.push(next); node = next;
    }
    while (trail.length && trail.length < 3 && node.children?.length === 1) { node = node.children[0]; trail.push(node); }
    return trail;
  }
  const go = (path: string[]) => { if (report.data) st.path = walk(report.data.tree, path).map((t) => t.name); };

  // Each top-level category keeps its color at every level below it.
  const colorOf = $derived(Object.fromEntries((report.data?.tree.children ?? []).map((c) => [c.name, catFill(c.name)])));
  const trail = $derived(report.data ? walk(report.data.tree, st.path) : []);
  const node = $derived(trail.length ? trail[trail.length - 1] : report.data?.tree);
  const color = $derived(trail.length ? colorOf[trail[0].name] : null);
  const leaf = $derived(!node?.children || trail.length >= 3);

  // At the bottom: the transactions behind the block you opened (again with `attempt`, after a failure).
  let attempt = $state(0);
  const where = $derived.by(() => {
    // A category spent only in its "general" part has merchants right below it: category > merchant.
    const merchantBelowTop = trail.length === 2 && !trail[1].children;
    const cat = trail.length >= 2 && !merchantBelowTop ? trail[1].name : trail[0]?.name;
    const merchant = trail.length >= 3 ? trail[2].name : merchantBelowTop ? trail[1].name : null;
    return { cat, merchant };
  });
  const txs = $derived.by(() => {
    void attempt;
    if (!report.data || !leaf || !report.data.tree.children.length) return null;
    const { start, end } = report.data;
    const qs = new URLSearchParams({ start, end, category: where.cat || "", merchant: where.merchant || "" });
    return api<ReportTx[]>(`/api/reports/transactions?${qs}`);
  });
  // One of them in Transactions: that day, that merchant (a "general" part is its category's).
  function open(t: ReportTx) {
    const cat = where.cat?.endsWith(" (general)") ? trail[0].name : where.cat;
    showTransactions(drill({ from: t.posted, to: t.posted, q: t.payee, category: cat ? catFilter(cat) : "" }));
  }
  const span = $derived(report.data ? { from: report.data.start, to: dayBefore(report.data.end) } : null);
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Period" value={st.range} options={rangeOptions} onchange={(v) => { st.range = v as RangeKey; report.load(); }} />
</div>

{#if !report.data || report.error || !node}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  <Card.Root aria-busy={report.loading} class={cn("transition-opacity", report.loading && "opacity-60")}>
    <Card.Header class="flex flex-wrap items-baseline justify-between gap-2">
      <nav aria-label="Where you are in the breakdown" class="flex flex-wrap items-baseline gap-1.5 text-sm">
        {#snippet crumb(label: string, depth: number)}
          <button class="cursor-pointer font-medium text-(--flow-out) hover:underline phone:min-h-11" onclick={() => go(st.path.slice(0, depth))}>{label}</button>
          <span class="text-muted-foreground" aria-hidden="true">›</span>
        {/snippet}
        {#if trail.length}
          {@render crumb("All spending", 0)}
          {#each trail.slice(0, -1) as t, i (i)}{@render crumb(t.name, i + 1)}{/each}
          <b class="font-semibold" aria-current="page">{trail[trail.length - 1].name}</b>
        {:else}
          <button class="cursor-pointer font-medium text-(--flow-out) hover:underline phone:min-h-11" aria-current="page" onclick={() => go([])}>All spending</button>
        {/if}
      </nav>
      <span class="flex items-baseline gap-3">
        {#if leaf && span && trail.length}
          <Button variant="link" size="sm" class="h-auto p-0 phone:min-h-11" title="See these in Transactions"
            onclick={() => showTransactions(drill({ ...span, q: where.merchant ?? "",
              category: where.cat ? catFilter(where.cat.endsWith(" (general)") ? trail[0].name : where.cat) : "" }))}>Transactions</Button>
        {/if}
        <span class="text-muted-foreground tabular-nums">{fmt0(node.value || 0)}</span>
      </span>
    </Card.Header>
    <Card.Content>
      {#if !d.tree.children.length}
        <p class="py-6 text-center text-sm text-muted-foreground">No spending in this period.</p>
      {:else if leaf}
        {#await txs}
          <div class="h-24 animate-pulse motion-reduce:animate-none rounded-lg bg-muted" role="status"><span class="sr-only">Loading…</span></div>
        {:then list}
          {#if list?.length}
            <table class="w-full text-sm">
              <tbody>
                {#each list as t, i (i)}
                  <tr class="relative border-t first:border-t-0 hover:bg-muted/50 [&>td]:py-2.5 [&>td]:pr-2">
                    <td class="whitespace-nowrap text-muted-foreground">{fmtDate(t.posted)}</td>
                    <td class="w-full max-w-0">
                      <button class="flex max-w-full cursor-pointer items-center text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                        title={`See it in Transactions: ${t.payee}`} onclick={() => open(t)}>
                        <span class="truncate">{t.payee}</span>{#if t.part}<span class="ml-1.5 shrink-0 rounded-md bg-muted px-1.5 py-px text-[11px] font-semibold text-muted-foreground">part of a split</span>{/if}
                      </button>
                    </td>
                    <td class="max-w-40 truncate text-muted-foreground max-md:hidden" title={t.account_name}>{t.account_name}</td>
                    <td class="text-right whitespace-nowrap tabular-nums">{fmt(t.amount)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="py-4 text-center text-sm text-muted-foreground">No transactions.</p>
          {/if}
        {:catch err}
          <Status compact error={err} retry={() => attempt++} what="these transactions" />
        {/await}
      {:else}
        {@const kids = node.children ?? []}
        <Treemap items={kids.map((c) => ({ ...c, color: color || colorOf[c.name] }))} total={node.value}
          onpick={(c) => go([...st.path, c.name])} />
        <details class="group mt-2" open={isPhone()}>
          <summary class="flex w-fit cursor-pointer list-none items-center gap-1.5 text-sm text-muted-foreground select-none hover:text-foreground phone:min-h-11 [&::-webkit-details-marker]:hidden">
            <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Show as table
          </summary>
          <table class="mt-2 w-full text-sm">
            <tbody>
              {#each kids as c (c.name)}
                <tr class="relative border-t hover:bg-muted/50 [&>td]:py-1.5">
                  <td class="w-full max-w-0">
                    <button class="flex max-w-full cursor-pointer items-center text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                      title={c.name} onclick={() => go([...st.path, c.name])}><Swatch color={color || colorOf[c.name]} /><span class="truncate">{c.name}</span></button>
                  </td>
                  <td class="pl-3 text-right whitespace-nowrap tabular-nums">{fmt0(c.value)}</td>
                  <td class="pl-3 text-right text-muted-foreground tabular-nums">{pct(c.value / node.value)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </details>
      {/if}
    </Card.Content>
  </Card.Root>
{/if}
