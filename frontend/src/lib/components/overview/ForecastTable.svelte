<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { fmt, fmtDow } from "$lib/format";
  import type { Overview } from "$lib/types";
  import { cn } from "$lib/utils";

  // The chart as a table, day by day: what goes in or out of the forecast's accounts, and the balance after it, in
  // the forecast and (when there are budgets) if you stick to your budget. Items that count in only one of the two
  // say so: the forecast's estimated card statements, and the budget's own spending and cards.
  let { fc }: { fc: Overview } = $props();

  type Item = { name: string; amount: number; note?: string; only?: "forecast" | "budget" };
  const alt = $derived(fc.budget?.total?.length === fc.total.length ? fc.budget.total : null);
  // The budget line equals the forecast until the first day they differ; it is shown from there on.
  const firstDiff = $derived(alt ? alt.findIndex((v, i) => Math.abs(v - fc.total[i]) >= 0.005) : -1);
  const showAlt = $derived(alt != null && firstDiff >= 0);

  const rows = $derived.by(() => {
    const byDate: Record<string, Item[]> = {};
    const add = (d: string, it: Item) => (byDate[d] ||= []).push(it);
    for (const e of fc.events) {
      const estCard = e.kind === "card" && !!e.estimated;
      add(e.date, { name: e.name + (e.estimated ? " (estimate)" : ""), amount: e.amount,
        only: alt && estCard ? "forecast" : undefined,
        note: alt && estCard ? "the budget line pays this card from its budgets instead" : undefined });
    }
    if (alt) {
      // budgets paid from a forecast account come out a little each day: one line a day, with each budget's share
      const spend: Record<string, { total: number; parts: string[] }> = {};
      for (const c of fc.budget?.changes ?? []) {
        if (c.kind === "budget") {
          const s = (spend[c.date] ||= { total: 0, parts: [] });
          s.total += c.amount; s.parts.push(`${c.name} ${fmt(-c.amount)}`);
        } else {
          add(c.date, { name: c.name + " (budgeted)", amount: c.amount, only: "budget",
            note: c.charged ? `${fmt(c.charged)} already on the card, the rest budgeted spending` : "budgeted spending" });
        }
      }
      for (const [d, s] of Object.entries(spend)) add(d, { name: "Budgeted spending", amount: Math.round(s.total * 100) / 100, only: "budget", note: s.parts.join(" · ") });
    }
    return fc.dates.map((d, i) => ({ d, i, items: (byDate[d] ?? []).sort((a, b) => a.amount - b.amount) }))
      .filter((r) => r.i === 0 || r.items.length);
  });
</script>

{#if showAlt}
  <p class="mt-2 flex justify-end gap-3 text-xs text-muted-foreground sm:hidden"><span>Forecast</span><span class="text-chart-2">On budget</span></p>
{/if}
<div class="mt-2 max-h-[32rem] overflow-auto rounded-md border">
  <table class="w-full text-sm">
    <thead class="sticky top-[env(safe-area-inset-top)] z-10 bg-card max-sm:hidden">
      <tr class="text-left text-xs text-muted-foreground [&>th]:px-3 [&>th]:py-2 [&>th]:font-medium">
        <th>Date</th><th>In and out</th><th class="text-right max-sm:hidden">Forecast</th>
        {#if showAlt}<th class="whitespace-nowrap text-right max-sm:hidden"><span class="inline-flex items-center gap-1.5"><i class="inline-block h-0 w-3 border-t-2 border-dashed border-chart-2"></i>On budget</span></th>{/if}
      </tr>
    </thead>
    <tbody>
      {#each rows as r (r.d)}
        <tr class="border-t align-top first:border-t-0 sm:first:border-t max-sm:flex max-sm:flex-col [&>td]:px-3 [&>td]:py-2 max-sm:[&>td]:py-1.5">
          <td class="whitespace-nowrap text-muted-foreground max-sm:flex max-sm:items-baseline max-sm:gap-3">
            {r.i === 0 ? "Today" : fmtDow(r.d)}
            <!-- on a phone each day is a block: the date and both balances on one line, what happens under it -->
            <span class={cn("ml-auto font-medium text-foreground tabular-nums sm:hidden", fc.total[r.i] < 0 && "text-destructive")}>{fmt(fc.total[r.i])}</span>
            {#if alt && showAlt && r.i >= firstDiff}<span class={cn("tabular-nums text-chart-2 sm:hidden", alt[r.i] < 0 && "text-destructive")}>{fmt(alt[r.i])}</span>{/if}
          </td>
          <td class="w-full sm:min-w-56">
            {#each r.items as it, j (j)}
              <div class="flex items-baseline justify-between gap-3">
                <span class="min-w-0" title={it.note}>
                  {it.name}
                  {#if it.only === "forecast"}<Badge variant="secondary" class="ml-1 px-1.5 py-0 text-[10px]">forecast only</Badge>{/if}
                  {#if it.only === "budget"}<Badge variant="outline" class="ml-1 border-chart-2/50 px-1.5 py-0 text-[10px] text-chart-2">budget only</Badge>{/if}
                </span>
                <span class={cn("shrink-0 tabular-nums", it.amount > 0 && "text-emerald-500")}>{it.amount > 0 ? "+" : "−"}{fmt(Math.abs(it.amount))}</span>
              </div>
            {:else}
              <span class="text-muted-foreground">{r.i === 0 ? "Where you start" : ""}</span>
            {/each}
          </td>
          <td class={cn("whitespace-nowrap text-right font-medium tabular-nums max-sm:hidden", fc.total[r.i] < 0 && "text-destructive")}>{fmt(fc.total[r.i])}</td>
          {#if alt && showAlt}<td class={cn("whitespace-nowrap text-right tabular-nums text-chart-2 max-sm:hidden", alt[r.i] < 0 && "text-destructive")}>{r.i >= firstDiff ? fmt(alt[r.i]) : ""}</td>{/if}
        </tr>
      {/each}
    </tbody>
  </table>
</div>
