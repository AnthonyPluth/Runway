<script lang="ts">
  import { api } from "$lib/api";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { catColor } from "$lib/categories.svelte";
  import { fmt, fmtDate } from "$lib/format";
  import { Report } from "./chart.svelte";
  import { rangeDates, rangeOptions, reportState as st, type RangeKey } from "./state.svelte";
  import Status from "./Status.svelte";
  import Swatch from "./Swatch.svelte";
  import Treemap from "./Treemap.svelte";
  import type { BreakdownNode, BreakdownReport, ReportTx } from "./types";

  // Breakdown: a treemap you click into: categories -> subcategories -> merchants -> transactions.
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
  const colorOf = $derived(Object.fromEntries((report.data?.tree.children ?? []).map((c, i) => [c.name, catColor(i)])));
  const trail = $derived(report.data ? walk(report.data.tree, st.path) : []);
  const node = $derived(trail.length ? trail[trail.length - 1] : report.data?.tree);
  const color = $derived(trail.length ? colorOf[trail[0].name] : null);
  const leaf = $derived(!node?.children || trail.length >= 3);

  // At the bottom: the transactions behind the block you opened.
  const txs = $derived.by(() => {
    if (!report.data || !leaf || !report.data.tree.children.length) return null;
    const { start, end } = report.data;
    // A category spent only in its "general" part has merchants right below it: category > merchant.
    const merchantBelowTop = trail.length === 2 && !trail[1].children;
    const cat = trail.length >= 2 && !merchantBelowTop ? trail[1].name : trail[0]?.name;
    const merchant = trail.length >= 3 ? trail[2].name : merchantBelowTop ? trail[1].name : null;
    const qs = new URLSearchParams({ start, end, category: cat || "", merchant: merchant || "" });
    return api<ReportTx[]>(`/api/reports/transactions?${qs}`);
  });
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Period" value={st.range} options={rangeOptions} onchange={(v) => { st.range = v as RangeKey; report.load(); }} />
</div>

{#if !report.data || report.error || !node}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  <Card.Root>
    <Card.Header class="flex flex-wrap items-baseline justify-between gap-2">
      <nav aria-label="Where you are in the breakdown" class="flex flex-wrap items-baseline gap-1.5 text-sm">
        {#snippet crumb(label: string, depth: number)}
          <button class="cursor-pointer font-medium text-(--flow-out) hover:underline" onclick={() => go(st.path.slice(0, depth))}>{label}</button>
          <span class="text-muted-foreground" aria-hidden="true">›</span>
        {/snippet}
        {#if trail.length}
          {@render crumb("All spending", 0)}
          {#each trail.slice(0, -1) as t, i (i)}{@render crumb(t.name, i + 1)}{/each}
          <b class="font-semibold" aria-current="page">{trail[trail.length - 1].name}</b>
        {:else}
          <button class="cursor-pointer font-medium text-(--flow-out) hover:underline" aria-current="page" onclick={() => go([])}>All spending</button>
        {/if}
      </nav>
      <span class="text-muted-foreground tabular-nums">{fmt(node.value || 0)}</span>
    </Card.Header>
    <Card.Content>
      {#if !d.tree.children.length}
        <p class="py-6 text-center text-sm text-muted-foreground">No spending in this period.</p>
      {:else if leaf}
        {#await txs}
          <p class="py-4 text-center text-sm text-muted-foreground">Loading…</p>
        {:then list}
          {#if list?.length}
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <tbody>
                  {#each list as t, i (i)}
                    <tr class="border-t first:border-t-0 [&>td]:py-2.5 [&>td]:pr-2">
                      <td class="whitespace-nowrap text-muted-foreground">{fmtDate(t.posted)}</td>
                      <td>{t.payee}{#if t.part}<span class="ml-1.5 rounded-md bg-muted px-1.5 py-px text-[11px] font-semibold text-muted-foreground">part of a split</span>{/if}</td>
                      <td class="text-muted-foreground max-sm:hidden">{t.account_name}</td>
                      <td class="text-right tabular-nums">{fmt(t.amount)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}
            <p class="py-4 text-center text-sm text-muted-foreground">No transactions.</p>
          {/if}
        {:catch err}
          <p class="text-sm">Something went wrong: {err.message}</p>
        {/await}
      {:else}
        {@const kids = node.children ?? []}
        <Treemap items={kids.map((c) => ({ ...c, color: color || colorOf[c.name] }))} total={node.value}
          onpick={(c) => go([...st.path, c.name])} />
        <p class="mt-2 text-sm text-muted-foreground">Each block is sized by what was spent. Click one to look inside.</p>
        <details class="mt-2">
          <summary class="cursor-pointer text-sm text-muted-foreground">Show as table</summary>
          <table class="mt-2 w-full text-sm">
            <tbody>
              {#each kids as c (c.name)}
                <tr class="border-t [&>td]:py-1.5">
                  <td class="whitespace-nowrap"><Swatch color={color || colorOf[c.name]} />{c.name}</td>
                  <td class="text-right tabular-nums">{fmt(c.value)}</td>
                  <td class="text-right text-muted-foreground tabular-nums">{Math.round((c.value / node.value) * 100)}%</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </details>
      {/if}
    </Card.Content>
  </Card.Root>
{/if}
