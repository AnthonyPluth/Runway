<script lang="ts">
  import { fmt0, shortMoney } from "$lib/format";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import { niceTicks, scrub } from "./chart.svelte";
  import Swatch from "./Swatch.svelte";
  import Tip from "./Tip.svelte";
  import type { BarSeries } from "./types";

  // A column per month. Stacked: several series stack with a 2px gap between them. Grouped: the series stand side
  // by side on one axis. Hovering a month (or touching it, and dragging sideways, or focusing the chart and using the
  // arrow keys) shows its breakdown. `partial`: the last month isn't over, so its columns are drawn faint and it's
  // labeled "(so far)". `avg`: a thin dashed line at the average the header quotes. `onpick`: clicking a column (or
  // its segment) opens what's behind it; on a touch screen the first tap shows the readout and a second one opens it.
  let { labels, series, grouped = false, height = 260, label, tipTitle, extra, partial = false, avg = null, onpick, pickTitle }: {
    labels: string[]; series: BarSeries[]; grouped?: boolean; height?: number; label: string;
    tipTitle: (i: number) => string; extra?: Snippet<[number]>; partial?: boolean; avg?: number | null;
    onpick?: (i: number, series: string | null) => void; pickTitle?: (i: number, series: string | null) => string;
  } = $props();

  let width = $state(0);
  // Drawn at its real width, so text stays at its real size (never scaled under 11px); 320 until it's measured.
  const W = $derived(width > 0 ? width : 320);
  const H = $derived(height), m = { top: 12, right: 8, bottom: 26, left: 52 };
  const iw = $derived(W - m.left - m.right), ih = $derived(H - m.top - m.bottom);
  const n = $derived(labels.length);
  const totals = $derived(labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0)));
  const hi = $derived(grouped ? Math.max(0, ...series.flatMap((s) => s.values)) : Math.max(0, ...totals));
  const ticks = $derived(niceTicks(0, hi, ih < 150 ? 3 : 4));
  const y1 = $derived(ticks[ticks.length - 1]);
  const y = (v: number) => m.top + (1 - v / y1) * ih;
  const bw = $derived(iw / Math.max(1, n));
  const every = $derived(Math.ceil(n / Math.max(1, Math.floor(iw / 44))));
  const cx = (i: number) => m.left + bw * i + bw / 2;
  const last = $derived(n - 1);
  const faint = (i: number) => partial && i === last;

  // The month labels along the bottom: every `every`th, and the last one when it doesn't run into the one before it
  // (with "(so far)" when that fits too).
  const textW = (s: string) => s.length * 6.6;
  const xLabels = $derived.by(() => {
    const out: { i: number; text: string }[] = [];
    for (let i = 0; i < last; i += every) out.push({ i, text: labels[i] });
    const prev = out.at(-1), prevRight = prev ? cx(prev.i) + textW(prev.text) / 2 : -Infinity;
    const fits = (text: string) => cx(last) - textW(text) / 2 >= prevRight + 6 && cx(last) + textW(text) / 2 <= W + 4;
    const tries = partial ? [`${labels[last]} (so far)`, labels[last]] : [labels[last]];
    const text = tries.find(fits);
    if (n && text) out.push({ i: last, text });
    return out;
  });

  // A column with a rounded top (radius r) from `bottom` up to `top`, between x0 and x1.
  const column = (x0: number, x1: number, top: number, bottom: number, r: number) => r
    ? `M${x0},${bottom} V${top + r} Q${x0},${top} ${x0 + r},${top} H${x1 - r} Q${x1},${top} ${x1},${top + r} V${bottom} Z`
    : `M${x0},${bottom} V${top} H${x1} V${bottom} Z`;
  const groupBox = (i: number) => {
    const gap = 2, w = Math.max(3, Math.min(18, (bw * 0.7 - gap * (series.length - 1)) / series.length));
    return { gap, w, x0: cx(i) - (series.length * w + gap * (series.length - 1)) / 2 };
  };

  const shapes = $derived.by(() => {
    const out: { d: string; color: string; faint: boolean }[] = [];
    for (let i = 0; i < n; i++) {
      if (grouped) {
        const g = groupBox(i);
        series.forEach((s, k) => {
          const v = s.values[i] || 0;
          if (v <= 0) return;
          const x0 = g.x0 + k * (g.w + g.gap), top = y(v), base = y(0);
          out.push({ d: column(x0, x0 + g.w, top, base, Math.min(3, base - top, g.w / 2)), color: s.color, faint: faint(i) });
        });
      } else {
        const w = Math.max(4, Math.min(40, bw * 0.62));
        const segs = series.map((s) => ({ s, v: s.values[i] || 0 })).filter((x) => x.v > 0);
        let base = 0;
        segs.forEach((x, k) => {
          // 2px surface-colored gap above the segment below; only the top of the column is rounded
          const top = y(base + x.v), bottom = y(base) - (k ? 2 : 0), h = Math.max(0, bottom - top);
          const r = k === segs.length - 1 ? Math.min(4, h) : 0;
          out.push({ d: column(cx(i) - w / 2, cx(i) + w / 2, top, bottom, r), color: x.s.color, faint: faint(i) });
          base += x.v;
        });
      }
    }
    return out;
  });

  let pointed = $state<number | null>(null), seg = $state<string | null>(null);
  let svgEl = $state<SVGSVGElement | null>(null), box = $state<HTMLDivElement | null>(null), tipX = $state(0);
  // The month the readout is on, while the chart still has it: on a phone the readout stays after the finger lifts,
  // and fewer months (12 to 6) would leave it past the last column, where it threw and stopped the chart redrawing.
  const hover = $derived(pointed != null && pointed < n ? pointed : null);

  // Which series is under the pointer in month i: the stacked segment at that height, or the grouped column there.
  function seriesAt(i: number, px: number, py: number): string | null {
    if (grouped) {
      const g = groupBox(i), k = Math.floor((px - g.x0) / (g.w + g.gap));
      return k >= 0 && k < series.length && (series[k].values[i] || 0) > 0 ? series[k].name : null;
    }
    const v = (1 - (py - m.top) / ih) * y1;
    let base = 0;
    for (const s of series) {
      const x = s.values[i] || 0;
      if (x <= 0) continue;
      if (v >= base && v <= base + x) return s.name;
      base += x;
    }
    return null;
  }
  function move(clientX: number, clientY?: number) {
    if (!svgEl) return;
    const r = svgEl.getBoundingClientRect(), k = W / (r.width || W);
    const px = (clientX - r.left) * k;
    pointed = Math.max(0, Math.min(n - 1, Math.floor((px - m.left) / bw)));
    seg = clientY == null ? null : seriesAt(pointed, px, (clientY - r.top) * k);
    tipX = clientX - r.left;
  }
  // Keyboard: the readout steps from month to month.
  function step(i: number) {
    pointed = Math.max(0, Math.min(n - 1, i)); seg = null;
    tipX = cx(pointed) * ((width || W) / W);
  }
  function key(e: KeyboardEvent) {
    const at = hover ?? last;
    const to = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: last }[e.key];
    if (to != null) { e.preventDefault(); step(to); return; }
    if ((e.key === "Enter" || e.key === " ") && onpick && hover != null) { e.preventDefault(); onpick(hover, null); }
    if (e.key === "Escape") pointed = null;
  }
  // A tap shows the readout (scrub sets it on touchstart); it opens only when it lands where the readout already was.
  let tapped: { i: number | null; s: string | null; touch: boolean } = { i: null, s: null, touch: false };
  function down(e: PointerEvent) { tapped = { i: hover, s: seg, touch: e.pointerType !== "mouse" }; }
  function click(e: MouseEvent) {
    if (!onpick) return;
    move(e.clientX, e.clientY);
    if (hover == null || (tapped.touch && (tapped.i !== hover || tapped.s !== seg))) return;
    onpick(hover, seg);
  }
  // A tap anywhere else puts the readout away.
  $effect(() => {
    if (hover == null || !box) return;
    const el = box;
    const away = (e: PointerEvent) => { if (!el.contains(e.target as Node)) pointed = null; };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  });
  const rows = (i: number) => series.map((s) => ({ s, v: s.values[i] || 0 })).filter((x) => x.v > 0).sort((a, b) => b.v - a.v);
</script>

<div class="relative" bind:this={box} bind:clientWidth={width}>
  {#if hi <= 0}
    <p class="py-4 text-center text-sm text-muted-foreground">Nothing to show for these months.</p>
  {:else}
    <svg bind:this={svgEl} viewBox={`0 0 ${W} ${H}`} class="block w-full select-none overflow-visible" role="img" aria-label={label}>
      {#each ticks as t (t)}
        <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" shape-rendering="crispEdges" />
        <text x={m.left - 8} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)" font-size="11.5" class="tabular-nums">{shortMoney(t)}</text>
      {/each}
      {#if hover != null}<rect x={m.left + bw * hover} y={m.top} width={bw} height={ih} fill="var(--foreground)" opacity="0.05" />{/if}
      {#each shapes as s, k (k)}<path d={s.d} style:fill={s.color} fill-opacity={s.faint ? 0.4 : undefined} data-partial={s.faint || undefined} />{/each}
      {#if avg != null && avg > 0}
        <line x1={m.left} x2={W - m.right} y1={y(avg)} y2={y(avg)} stroke="var(--foreground)" stroke-opacity="0.55" stroke-width="1"
          stroke-dasharray="4 3" shape-rendering="crispEdges" data-average />
      {/if}
      {#each xLabels as l (l.i)}
        <text x={cx(l.i)} y={H - 8} text-anchor="middle" fill="var(--muted-foreground)" font-size="11.5">{l.text}</text>
      {/each}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" data-overlay tabindex="0" role="slider"
        aria-label={label} aria-valuemin={0} aria-valuemax={last} aria-valuenow={hover ?? last}
        aria-valuetext={`${tipTitle(hover ?? last)}: ${fmt0(grouped ? series[0]?.values[hover ?? last] : totals[hover ?? last])}`}
        class={cn("outline-none focus-visible:stroke-ring focus-visible:stroke-2", onpick && "cursor-pointer")}
        onmousemove={(e) => move(e.clientX, e.clientY)} onmouseleave={() => (pointed = null)} use:scrub={move}
        onpointerdown={down} onclick={click} onkeydown={key} onfocus={() => { if (hover == null) step(last); }} onblur={() => (pointed = null)}>
        {#if onpick && pickTitle && hover != null}<title>{pickTitle(hover, seg)}</title>{/if}
      </rect>
    </svg>
    {#if hover != null}
      <Tip x={tipX} boxWidth={width}>
        <div class="text-muted-foreground">{tipTitle(hover)}</div>
        {#if grouped}
          {#each series as s (s.name)}
            <div class={cn("flex justify-between gap-3 text-muted-foreground", seg === s.name && "text-foreground")}><span class="flex items-center"><Swatch color={s.color} />{s.name}</span><span class="tabular-nums">{fmt0(s.values[hover] || 0)}</span></div>
          {/each}
          {@render extra?.(hover)}
        {:else}
          <div class="text-[15px] font-semibold tabular-nums">{fmt0(totals[hover])}</div>
          {#if series.length > 1}
            {#each rows(hover) as x (x.s.name)}
              <div class={cn("flex justify-between gap-3 text-muted-foreground", seg === x.s.name && "text-foreground")}><span class="flex min-w-0 items-center"><Swatch color={x.s.color} /><span class="truncate">{x.s.name}</span></span><span class="tabular-nums">{fmt0(x.v)}</span></div>
            {/each}
          {/if}
        {/if}
      </Tip>
    {/if}
  {/if}
</div>
