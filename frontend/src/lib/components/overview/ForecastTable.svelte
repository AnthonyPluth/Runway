<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Popover } from "bits-ui";
  import { fmt, fmtDow } from "$lib/format";
  import type { Overview } from "$lib/types";
  import { cn } from "$lib/utils";

  // The chart as a table, day by day: what goes in or out of the forecast's accounts, and the balance after it, in
  // the forecast and (when there are budgets) if you stick to your budget. Items that count in only one of the two
  // say so: the forecast's estimated card statements, and the budget's own spending and cards.
  let { fc }: { fc: Overview } = $props();

  // `parts`: what a day's budgeted spending is made of, one line a budget, shown when you hover or tap it.
  type Item = { name: string; amount: number; note?: string; only?: "forecast" | "budget"; parts?: { name: string; amount: number }[] };
  const alt = $derived(fc.budget?.total?.length === fc.total.length ? fc.budget.total : null);
  // The budget line equals the forecast until the first day they differ; it is shown from there on.
  const firstDiff = $derived(alt ? alt.findIndex((v, i) => Math.abs(v - fc.total[i]) >= 0.005) : -1);
  const showAlt = $derived(alt != null && firstDiff >= 0);

  const rows = $derived.by(() => {
    const byDate: Record<string, Item[]> = {};
    const add = (d: string, it: Item) => (byDate[d] ||= []).push(it);
    // A card payment both lines make alike (same card, day and amount: its statements are its budgets plus its other
    // spending on both) is one line, not one of each.
    const same = (e: { date: string; name: string; amount: number }) => (c: { kind: string; date: string; name: string; amount: number }) =>
      c.kind === "card" && c.date === e.date && c.name === e.name && Math.abs(c.amount - e.amount) < 0.005;
    const shared = new Set<unknown>();
    for (const e of fc.events) {
      const estCard = e.kind === "card" && !!e.estimated;
      const both = alt && estCard ? fc.budget?.changes?.find(same(e)) : undefined;
      if (both) shared.add(both);
      add(e.date, { name: e.name + (e.estimated ? " (estimate)" : ""), amount: e.amount,
        only: alt && estCard && !both ? "forecast" : undefined,
        note: both ? "the same on the budget line: the card's budgets plus its other spending"
          : alt && estCard ? "the budget line pays this card from its budgets instead" : undefined });
    }
    if (alt) {
      // budgets paid from a forecast account come out a little each day: one line a day, made of each budget's share
      const spend: Record<string, { total: number; parts: { name: string; amount: number }[] }> = {};
      for (const c of fc.budget?.changes ?? []) {
        if (shared.has(c)) continue;
        if (c.kind === "budget") {
          const s = (spend[c.date] ||= { total: 0, parts: [] });
          s.total += c.amount; s.parts.push({ name: c.name, amount: -c.amount });
        } else {
          add(c.date, { name: c.name + " (budgeted)", amount: c.amount, only: "budget",
            note: [c.charged ? `${fmt(c.charged)} already on the card, the rest budgeted spending` : "budgeted spending",
              c.assumed_cycle ? "no statement yet, so taken to close at the month’s end and be paid 25 days later" : ""].filter(Boolean).join(" · ") });
        }
      }
      for (const [d, s] of Object.entries(spend)) add(d, { name: "Budgeted spending", amount: Math.round(s.total * 100) / 100, only: "budget",
        parts: s.parts.sort((a, b) => b.amount - a.amount) });
    }
    return fc.dates.map((d, i) => ({ d, i, items: (byDate[d] ?? []).sort((a, b) => a.amount - b.amount) }))
      .filter((r) => r.i === 0 || r.items.length);
  });
  // How a day's share is worked out: this month, what's left of each budget over the days left in it; later months,
  // each budget over the month's days. (A budget's recurring payments are already in the forecast, so they're left out.)
  const how = (d: string) => d.slice(0, 7) === fc.dates[0]?.slice(0, 7)
    ? "What’s left of each budget this month, spread evenly over the days left in it."
    : "Each monthly budget, spread evenly over the days of the month.";
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
                  {#if it.parts}
                    <Popover.Root>
                      <Popover.Trigger openOnHover openDelay={150}
                        class="cursor-help underline decoration-muted-foreground/50 decoration-dotted underline-offset-4 hover:decoration-foreground focus-visible:outline-none focus-visible:decoration-foreground">{it.name}</Popover.Trigger>
                      <Popover.Portal>
                        <Popover.Content side="bottom" align="start" sideOffset={6}
                          class="z-50 w-72 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-lg outline-none">
                          <p class="text-xs text-muted-foreground">{how(r.d)} Recurring payments in a budget’s category are left out: the forecast already has them. Only the On budget line counts this.</p>
                          <ul class="mt-2 space-y-0.5">
                            {#each it.parts as p, k (k)}
                              <li class="flex justify-between gap-3"><span class="min-w-0 truncate">{p.name}</span><span class="shrink-0 tabular-nums">{fmt(p.amount)}</span></li>
                            {/each}
                          </ul>
                          <div class="mt-1.5 flex justify-between gap-3 border-t pt-1.5 font-medium"><span>That day</span><span class="tabular-nums">{fmt(-it.amount)}</span></div>
                        </Popover.Content>
                      </Popover.Portal>
                    </Popover.Root>
                  {:else}{it.name}{/if}
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
