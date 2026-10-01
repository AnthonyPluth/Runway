<script lang="ts">
  import { fmt, shortMoney } from "$lib/format";
  import type { Snippet } from "svelte";
  import { niceTicks, scrub } from "./chart.svelte";
  import Swatch from "./Swatch.svelte";
  import Tip from "./Tip.svelte";
  import type { BarSeries } from "./types";

  // A column per month. Stacked: several series stack with a 2px gap between them. Grouped: the series stand side
  // by side on one axis. Hovering a month (or touching it, and dragging sideways) shows its breakdown.
  let { labels, series, grouped = false, height = 260, label, tipTitle, extra }: {
    labels: string[]; series: BarSeries[]; grouped?: boolean; height?: number; label: string;
    tipTitle: (i: number) => string; extra?: Snippet<[number]>;
  } = $props();

  let width = $state(0);
  const H = $derived(height), m = { top: 12, right: 8, bottom: 26, left: 52 };
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = $derived(H - m.top - m.bottom);
  const n = $derived(labels.length);
  const totals = $derived(labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0)));
  const hi = $derived(grouped ? Math.max(0, ...series.flatMap((s) => s.values)) : Math.max(0, ...totals));
  const ticks = $derived(niceTicks(0, hi, 4));
  const y1 = $derived(ticks[ticks.length - 1]);
  const y = (v: number) => m.top + (1 - v / y1) * ih;
  const bw = $derived(iw / Math.max(1, n));
  const every = $derived(Math.ceil(n / Math.max(1, Math.floor(iw / 44))));
  const cx = (i: number) => m.left + bw * i + bw / 2;

  // A column with a rounded top (radius r) from `bottom` up to `top`, between x0 and x1.
  const column = (x0: number, x1: number, top: number, bottom: number, r: number) => r
    ? `M${x0},${bottom} V${top + r} Q${x0},${top} ${x0 + r},${top} H${x1 - r} Q${x1},${top} ${x1},${top + r} V${bottom} Z`
    : `M${x0},${bottom} V${top} H${x1} V${bottom} Z`;

  const shapes = $derived.by(() => {
    const out: { d: string; color: string }[] = [];
    for (let i = 0; i < n; i++) {
      if (grouped) {
        const gap = 2, w = Math.max(3, Math.min(18, (bw * 0.7 - gap * (series.length - 1)) / series.length));
        const groupW = series.length * w + gap * (series.length - 1);
        series.forEach((s, k) => {
          const v = s.values[i] || 0;
          if (v <= 0) return;
          const x0 = cx(i) - groupW / 2 + k * (w + gap), top = y(v), base = y(0);
          out.push({ d: column(x0, x0 + w, top, base, Math.min(3, base - top, w / 2)), color: s.color });
        });
      } else {
        const w = Math.max(4, Math.min(40, bw * 0.62));
        const segs = series.map((s) => ({ s, v: s.values[i] || 0 })).filter((x) => x.v > 0);
        let base = 0;
        segs.forEach((x, k) => {
          // 2px surface-colored gap above the segment below; only the top of the column is rounded
          const top = y(base + x.v), bottom = y(base) - (k ? 2 : 0), h = Math.max(0, bottom - top);
          const r = k === segs.length - 1 ? Math.min(4, h) : 0;
          out.push({ d: column(cx(i) - w / 2, cx(i) + w / 2, top, bottom, r), color: x.s.color });
          base += x.v;
        });
      }
    }
    return out;
  });

  let pointed = $state<number | null>(null), svgEl = $state<SVGSVGElement | null>(null), tipX = $state(0);
  // The month the readout is on, while the chart still has it: on a phone the readout stays after the finger lifts,
  // and fewer months (12 to 6) would leave it past the last column, where it threw and stopped the chart redrawing.
  const hover = $derived(pointed != null && pointed < n ? pointed : null);
  function move(clientX: number) {
    if (!svgEl) return;
    const r = svgEl.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    pointed = Math.max(0, Math.min(n - 1, Math.floor((px - m.left) / bw)));
    tipX = clientX - r.left;
  }
  const rows = (i: number) => series.map((s) => ({ s, v: s.values[i] || 0 })).filter((x) => x.v > 0).sort((a, b) => b.v - a.v);
</script>

<div class="relative" bind:clientWidth={width}>
  {#if hi <= 0}
    <p class="py-4 text-center text-sm text-muted-foreground">Nothing to show for these months.</p>
  {:else}
    <svg bind:this={svgEl} viewBox={`0 0 ${W} ${H}`} class="block w-full select-none overflow-visible" role="img" aria-label={label}>
      {#each ticks as t (t)}
        <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" shape-rendering="crispEdges" />
        <text x={m.left - 8} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)" font-size="11.5" class="tabular-nums">{shortMoney(t)}</text>
      {/each}
      {#if hover != null}<rect x={m.left + bw * hover} y={m.top} width={bw} height={ih} fill="var(--foreground)" opacity="0.05" />{/if}
      {#each shapes as s, k (k)}<path d={s.d} style:fill={s.color} />{/each}
      {#each labels as l, i (i)}
        {#if i % every === 0 || i === n - 1}
          <text x={cx(i)} y={H - 8} text-anchor="middle" fill="var(--muted-foreground)" font-size="11.5">{l}</text>
        {/if}
      {/each}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" class="cursor-pointer" role="presentation"
        onmousemove={(e) => move(e.clientX)} onmouseleave={() => (pointed = null)} use:scrub={move} />
    </svg>
    {#if hover != null}
      <Tip x={tipX} boxWidth={width}>
        <div class="text-muted-foreground">{tipTitle(hover)}</div>
        {#if grouped}
          {#each series as s (s.name)}
            <div class="flex justify-between gap-3 text-muted-foreground"><span class="flex items-center"><Swatch color={s.color} />{s.name}</span><span class="tabular-nums">{fmt(s.values[hover] || 0)}</span></div>
          {/each}
          {@render extra?.(hover)}
        {:else}
          <div class="text-[15px] font-semibold tabular-nums">{fmt(totals[hover])}</div>
          {#if series.length > 1}
            {#each rows(hover) as x (x.s.name)}
              <div class="flex justify-between gap-3 text-muted-foreground"><span class="flex items-center"><Swatch color={x.s.color} />{x.s.name}</span><span class="tabular-nums">{fmt(x.v)}</span></div>
            {/each}
          {/if}
        {/if}
      </Tip>
    {/if}
  {/if}
</div>
