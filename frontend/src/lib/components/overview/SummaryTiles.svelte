<script lang="ts">
  import { api } from "$lib/api";
  import { gainCls, signed } from "$lib/components/investments/numbers";
  import type { NetWorth } from "$lib/components/networth/types";
  import { fmt0, fmtDate, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";

  // One tile under the balance card, a link: net worth with its last month. It loads on its own and says nothing if it
  // can't (the card above is the main thing). This month's spending is in the "This month" card below.
  const month = thisMonth();
  const loadWorth = () => api<NetWorth>("/api/networth");
  let worth = $state(loadWorth());

  const tile = "tile block min-w-0 transition-colors hover:bg-white/4 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

  // Net worth's line is the history since the start of this month. Its change is measured from the snapshot on or before
  // the month's first day, and only from that: snapshots are saved when the numbers are viewed, so an older one would
  // pass an earlier month's change off as this month's.
  function sinceMonthStart(h: NetWorth["history"]) {
    const start = `${month}-01`;
    const before = h.filter((p) => p.date <= start).at(-1);
    return { points: [...(before ? [before] : []), ...h.filter((p) => p.date > start)], base: before ?? null };
  }
  // Days from a snapshot to the month's first day: past a few, the change is labelled with where it starts from.
  const gapDays = (d: string) => Math.round((Date.parse(`${month}-01T00:00:00Z`) - Date.parse(`${d}T00:00:00Z`)) / 864e5);
  const W = 120, H = 32;
  function line(vals: number[]): string {
    if (vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    return vals.map((v, i) => `${((i / (vals.length - 1)) * W).toFixed(1)},${(H - 3 - ((v - lo) / span) * (H - 6)).toFixed(1)}`).join(" ");
  }
</script>

<div data-testid="summary-tiles">
  {#await worth}
    <div class="h-32 animate-pulse motion-reduce:animate-none rounded-2xl bg-card"></div>
  {:then nw}
    {@const since = sinceMonthStart(nw.history)}
    {@const change = since.base ? nw.net - since.base.net : null}
    {@const path = line(since.points.map((p) => p.net))}
    <a href="#networth" class={tile}>
      <span class="flex items-center justify-between text-[13px] text-muted-foreground">Net worth<ChevronRight class="size-4" aria-hidden="true" /></span>
      <span class="mt-1 block text-[24px] leading-tight font-semibold tracking-tight tabular-nums">{fmt0(nw.net)}</span>
      {#if path}
        <svg viewBox={`0 0 ${W} ${H}`} class={cn("mt-1.5 h-8 w-full", (change ?? 0) >= 0 ? "text-good" : "text-loss")} preserveAspectRatio="none" aria-hidden="true">
          <polyline points={path} fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
        </svg>
      {/if}
      <span class={cn("mt-1.5 block text-[13px] font-medium tabular-nums", gainCls(change) || "text-muted-foreground")}>
        {#if change == null}No change yet this month{:else}{signed(change)}<span class="font-normal text-muted-foreground"> {since.base && since.base.date < `${month}-01` && gapDays(since.base.date) > 3 ? `since ${fmtDate(since.base.date, { month: "short", day: "numeric", year: "numeric" })}` : "this month"}</span>{/if}
      </span>
    </a>
  {:catch}
    <div class="tile flex min-w-0 flex-col justify-center gap-1 text-[13px] text-muted-foreground" data-failed="networth">
      <span>Couldn’t load net worth</span>
      <button type="button" class="self-start font-medium text-primary" onclick={() => (worth = loadWorth())}>Try again</button>
    </div>
  {/await}
</div>
