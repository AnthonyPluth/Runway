<script lang="ts">
  import { api } from "$lib/api";
  import { gainCls, signed } from "$lib/components/investments/numbers";
  import type { NetWorth } from "$lib/components/networth/types";
  import { apiCall } from "$lib/contract";
  import { barWidth, fmt0, monthShort, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";

  // Two tiles under the balance card, each a link: this month's spending against the budgets, and net worth with its
  // last month. Each loads on its own and says nothing if it can't (the card above is the main thing).
  interface Pace { month: string; prev_month: string; spent: number; last_same_point: number }
  const month = thisMonth();
  const spending = Promise.all([api<Pace>("/api/month_pace"), apiCall<"GET /api/budget">(`/api/budget?month=${month}`)]);
  const worth = api<NetWorth>("/api/networth");

  const tile = "tile block min-w-0 transition-colors hover:bg-white/4 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

  // Net worth's line is the history since the start of this month (a point just before it, so the line has a start).
  function sinceMonthStart(h: NetWorth["history"]) {
    const start = `${month}-01`;
    const before = h.filter((p) => p.date < start).at(-1);
    return { points: [...(before ? [before] : []), ...h.filter((p) => p.date >= start)], base: before ?? null };
  }
  const W = 120, H = 32;
  function line(vals: number[]): string {
    if (vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    return vals.map((v, i) => `${((i / (vals.length - 1)) * W).toFixed(1)},${(H - 3 - ((v - lo) / span) * (H - 6)).toFixed(1)}`).join(" ");
  }
</script>

<div class="grid grid-cols-2 gap-3" data-testid="summary-tiles">
  {#await spending}
    <div class="h-32 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div>
  {:then [pace, budget]}
    <!-- The bar is what the budgeted categories have spent of what they're budgeted; the marker is how far through the
         month we are, so a bar past the marker is ahead of pace. With no budgets, the pace is last month's at this point. -->
    {@const budgeted = budget.categories.filter((c) => c.depth === 0 && c.budget && c.budget > 0)}
    {@const total = budgeted.reduce((s, c) => s + (c.available ?? c.budget!), 0)}
    {@const used = budgeted.reduce((s, c) => s + c.spent, 0)}
    {@const through = budget.days_in_month ? budget.day / budget.days_in_month : 0}
    {@const ahead = used - total * through}
    {@const prev = pace.spent - pace.last_same_point}
    <a href="#budget" class={tile} aria-label={`Spending this month, ${fmt0(pace.spent)}. Open Budget`}>
      <span class="flex items-center justify-between text-[13px] text-muted-foreground">Spent in {monthShort(pace.month)}<ChevronRight class="size-4" aria-hidden="true" /></span>
      <span class="mt-1 block text-[24px] leading-tight font-semibold tracking-tight tabular-nums">{fmt0(pace.spent)}</span>
      {#if total > 0}
        <span class="relative mt-2.5 block h-1.5 rounded-full bg-muted" role="img"
          aria-label={`${fmt0(used)} of ${fmt0(total)} budgeted, ${Math.round(through * 100)}% through the month`}>
          <span class={cn("block h-full rounded-full", used > total ? "bg-destructive" : "bg-primary")} style:width={barWidth(used / total)}></span>
          <span class="absolute -top-1 h-3.5 w-0.5 rounded-full bg-foreground" style:left={barWidth(through)} data-pace-marker></span>
        </span>
        <span class={cn("mt-2 block text-[13px] font-medium tabular-nums", Math.abs(ahead) < 1 ? "text-muted-foreground" : ahead > 0 ? "text-warning" : "text-good")}>
          {#if Math.abs(ahead) < 1}On pace{:else}{fmt0(Math.abs(ahead))} {ahead > 0 ? "over" : "under"} pace{/if}
        </span>
      {:else}
        <span class={cn("mt-2 block text-[13px] font-medium tabular-nums", Math.abs(prev) < 1 ? "text-muted-foreground" : prev > 0 ? "text-warning" : "text-good")}>
          {#if Math.abs(prev) < 1}Same as this point in {monthShort(pace.prev_month)}{:else}{fmt0(Math.abs(prev))} {prev > 0 ? "over" : "under"} {monthShort(pace.prev_month)}’s pace{/if}
        </span>
      {/if}
    </a>
  {:catch}
    <div class="hidden"></div>
  {/await}

  {#await worth}
    <div class="h-32 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div>
  {:then nw}
    {@const since = sinceMonthStart(nw.history)}
    {@const change = since.base ? nw.net - since.base.net : nw.change["30d"]}
    {@const path = line(since.points.map((p) => p.net))}
    <a href="#networth" class={tile} aria-label={`Net worth, ${fmt0(nw.net)}. Open Net worth`}>
      <span class="flex items-center justify-between text-[13px] text-muted-foreground">Net worth<ChevronRight class="size-4" aria-hidden="true" /></span>
      <span class="mt-1 block text-[24px] leading-tight font-semibold tracking-tight tabular-nums">{fmt0(nw.net)}</span>
      {#if path}
        <svg viewBox={`0 0 ${W} ${H}`} class={cn("mt-1.5 h-8 w-full", (change ?? 0) >= 0 ? "text-good" : "text-loss")} preserveAspectRatio="none" aria-hidden="true">
          <polyline points={path} fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
        </svg>
      {/if}
      <span class={cn("mt-1.5 block text-[13px] font-medium tabular-nums", gainCls(change) || "text-muted-foreground")}>
        {#if change == null}No change yet{:else}{signed(change)}<span class="font-normal text-muted-foreground"> {since.base ? "this month" : "in 30 days"}</span>{/if}
      </span>
    </a>
  {:catch}
    <div class="hidden"></div>
  {/await}
</div>
