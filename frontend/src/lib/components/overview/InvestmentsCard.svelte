<script lang="ts">
  import type { InvestmentsSummary } from "$lib/api-types";
  import { apiCall } from "$lib/contract";
  import LiveDot from "$lib/components/investments/LiveDot.svelte";
  import { livePrices } from "$lib/components/investments/live";
  import { gainCls, pct, signed } from "$lib/components/investments/numbers";
  import type { Quote } from "$lib/components/investments/types";
  import { Button } from "$lib/components/ui/button";
  import { fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";
  import { applyLive } from "./investmentsCard";

  // The Overview's Investments card: the total, today's change, the last month's trend and the biggest movers. The
  // whole card opens Investments. If it can't load it says so rather than show figures that may be old; with no
  // investment accounts there is no card.
  let base = $state.raw<InvestmentsSummary | null>(null);
  let shown = $state.raw<InvestmentsSummary | null>(null);
  let failed = $state(false);
  let market = $state<string | null>(null);   // set once live prices have come: the dot shows while the market is open

  function load() {
    failed = false;
    apiCall<"GET /api/investments/summary">("/api/investments/summary").then(
      (s) => { base = shown = s; market = null; },
      () => { failed = true; base = shown = null; });   // no figures from before: they would look current
  }
  load();

  // Live prices while the Overview is open: they re-price the total and today's change. Leaving the page stops them.
  $effect(() => {
    const b = base;
    if (!b || b.empty) return;
    const quotes: Record<string, Quote> = {};
    return livePrices((l) => {
      Object.assign(quotes, l.quotes);
      shown = applyLive(b, quotes, l.market);
      market = l.market;
    });
  });

  const s = $derived(shown);
  const arrow = (x: number | null | undefined) => (x == null || x === 0 ? "" : x > 0 ? "▲ " : "▼ ");
  const W = 96, H = 36;
  const line = $derived.by(() => {
    const t = s?.trend ?? [];
    if (t.length < 2) return "";
    const lo = Math.min(...t), hi = Math.max(...t), span = hi - lo || 1;
    return t.map((v, i) => `${((i / (t.length - 1)) * W).toFixed(1)},${(H - 3 - ((v - lo) / span) * (H - 6)).toFixed(1)}`).join(" ");
  });
  const trendUp = $derived((s?.trend.at(-1) ?? 0) >= (s?.trend[0] ?? 0));
</script>

{#if failed}
  <section class="min-w-0" data-testid="investments-card">
    <h2 class="eyebrow mb-1.5 px-4">Investments</h2>
    <div class="group-list"><div class="cell text-sm text-muted-foreground">
      <span class="min-w-0 flex-1">Couldn't load investments ·</span>
      <Button variant="link" size="sm" class="h-auto px-0" onclick={load}>Try again</Button>
    </div></div>
  </section>
{:else if s && !s.empty}
  <section class="min-w-0" data-testid="investments-card">
    <a href="#investments" class="group-list block transition-colors hover:bg-white/4" aria-label="Investments">
      <div class="px-4 pt-3 pb-3.5">
        <div class="flex items-center gap-2">
          <h2 class="eyebrow">Investments</h2>
          {#if market === "open"}<span class="inline-flex items-center gap-1.5 text-[12px] text-foreground/80"><LiveDot /> Live</span>{/if}
        </div>
        <div class="mt-1 flex items-end justify-between gap-3">
          <div class="min-w-0">
            <div class="text-[28px] font-semibold tracking-tight tabular-nums">{fmt0(s.total)}</div>
            {#if s.day_change != null}
              <p class={cn("text-[13px] font-medium tabular-nums", gainCls(s.day_change) || "text-muted-foreground")}>
                <span aria-hidden="true">{arrow(s.day_change)}</span>{signed(s.day_change)}{#if s.day_change_pct != null} ({pct(s.day_change_pct, 2)}){/if}
                <span class="font-normal text-muted-foreground"> today</span>
              </p>
            {/if}
          </div>
          {#if line}
            <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} class={cn("shrink-0", trendUp ? "text-good" : "text-loss")} role="img" aria-label="Value over the last month">
              <polyline points={line} fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
            </svg>
          {/if}
        </div>
        {#if s.movers.length}
          <ul class="mt-3 flex flex-wrap gap-1.5" aria-label="Biggest movers today">
            {#each s.movers as m (m.ticker)}
              <li class="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-[12px] tabular-nums">
                <span class="font-semibold">{m.ticker}</span><span class={gainCls(m.day_change_pct)}>{pct(m.day_change_pct, 1)}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    </a>
  </section>
{/if}
