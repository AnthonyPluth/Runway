<script lang="ts">
  import { fmt, fmtDate, fmtSigned, fmtDow, parseDate, shortMoney } from "$lib/format";
  import type { ForecastEvent, Overview } from "$lib/types";

  // The projected balance, day by day: what each day brings (hover or drag for a day's readout).
  let { fc }: { fc: Overview } = $props();

  let width = $state(0);
  const H = 280, m = { top: 30, right: 8, bottom: 28, left: 52 };
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = H - m.top - m.bottom;
  const series = $derived(fc.total);
  const n = $derived(series.length);

  // The days on show, v0 to v1: every day of the forecast.
  const v0 = 0, v1 = $derived(Math.max(0, n - 1));
  const inView = (i: number) => i >= v0 && i <= v1;
  const shown = (vals: number[]) => vals.slice(v0, v1 + 1);

  function niceTicks(min: number, max: number, count = 5): number[] {
    const span = max - min || Math.abs(max) || 1, raw = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw)!;
    const start = Math.floor(min / step) * step, end = Math.ceil(max / step) * step;
    const out: number[] = [];
    for (let v = start; v <= end + step / 2; v += step) out.push(Math.round(v * 100) / 100);
    return out;
  }
  const ticks = $derived.by(() => {
    // The axis always starts at $0, so the line's height is the balance; it only goes lower to show
    // a balance that dips below zero.
    const lo = Math.min(0, ...shown(series)), hi = Math.max(0, ...shown(series));
    return niceTicks(lo, hi);
  });
  const y0 = $derived(ticks[0]), y1 = $derived(ticks[ticks.length - 1]);
  const x = (i: number) => m.left + ((i - v0) / Math.max(1, v1 - v0)) * iw;
  const y = (v: number) => m.top + (1 - (v - y0) / (y1 - y0 || 1)) * ih;
  const path = (vals: number[]) => shown(vals).map((v, k) => `${x(v0 + k).toFixed(1)},${y(v).toFixed(1)}`).join(" L");

  const eventsByDate = $derived.by(() => {
    const by: Record<string, ForecastEvent[]> = {};
    for (const e of fc.events) (by[e.date] ||= []).push(e);
    return by;
  });
  // Labels along the bottom: the first of each month, or (a forecast of a few weeks) evenly spaced days.
  const months = $derived.by(() => {
    const span = v1 - v0, out: { i: number; label: string }[] = [];
    const first = { i: v0, label: v0 === 0 ? "Today" : fmtDate(fc.dates[v0]) };
    if (span <= 45) {
      const step = Math.max(1, Math.ceil(span / Math.max(1, Math.floor(iw / 72))));
      for (let i = v0; i <= v1; i += step) out.push(i === v0 ? first : { i, label: fmtDate(fc.dates[i]) });
      return out;
    }
    out.push(first);
    for (let i = v0 + 1; i <= v1; i++) {
      const dt = parseDate(fc.dates[i]);
      if (dt.getDate() === 1 && x(i) - m.left > 56) out.push({ i, label: dt.toLocaleDateString("en-US", { month: "short" }) });
    }
    return out;
  });
  // Hovering (or dragging sideways on a phone) shows that day's balance and what happens on it.
  let pointed = $state<number | null>(null), svgEl = $state<SVGSVGElement | null>(null), tipEl = $state<HTMLDivElement | null>(null);
  // The day the readout is on, while the chart still has it (a shorter one would leave it past the end).
  const hover = $derived(pointed != null && pointed < n ? pointed : null);
  // Dragging sideways on a phone moves the readout; dragging up or down still scrolls the page. (Svelte's own touch
  // handlers are passive, so they couldn't stop the page scrolling sideways.)
  function sideways(el: SVGElement, onMove: (clientX: number) => void) {
    let start: Touch | null = null;
    const down = (e: TouchEvent) => { start = e.touches[0]; };
    const drag = (e: TouchEvent) => {
      const t = e.touches[0];
      if (start && Math.abs(t.clientY - start.clientY) > Math.abs(t.clientX - start.clientX)) return;
      onMove(t.clientX);
      if (e.cancelable) e.preventDefault();
    };
    el.addEventListener("touchstart", down, { passive: true });
    el.addEventListener("touchmove", drag, { passive: false });
    return { destroy() { el.removeEventListener("touchstart", down); el.removeEventListener("touchmove", drag); } };
  }
  /** The day under a point on screen. */
  function dayAt(clientX: number): number {
    const r = svgEl!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    return Math.max(v0, Math.min(v1, v0 + Math.round(((px - m.left) / iw) * (v1 - v0))));
  }
  function move(clientX: number) { if (svgEl) pointed = dayAt(clientX); }

  const tipPos = $derived.by(() => {
    if (hover == null || !svgEl) return { left: 0, top: 0 };
    const r = svgEl.getBoundingClientRect(), tw = tipEl?.offsetWidth ?? 180;
    return { left: Math.min(Math.max(0, (x(hover) / W) * r.width + 12), r.width - tw), top: Math.max(0, (y(series[hover]) / H) * r.height - 60) };
  });
</script>

<div class="relative" bind:clientWidth={width}>
  {#if !n}
    <p class="py-6 text-center text-sm text-muted-foreground">No cash accounts in the forecast yet.</p>
  {:else}
    <svg bind:this={svgEl} viewBox={`0 0 ${W} ${H}`} class="block w-full select-none text-xs" role="img"
      aria-label={`Projected balance over the next ${n - 1} days`}>
      <defs>
        <linearGradient id="fc-area" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="var(--chart-1)" stop-opacity="0.22" /><stop offset="1" stop-color="var(--chart-1)" stop-opacity="0" />
        </linearGradient>
      </defs>
      {#each ticks as t (t)}
        <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" />
        <text x={m.left - 8} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)">{shortMoney(t)}</text>
      {/each}
      {#each months as ml (ml.i)}
        <text x={x(ml.i)} y={H - 8} text-anchor={ml.i === 0 ? "start" : "middle"} fill="var(--muted-foreground)">{ml.label}</text>
      {/each}
      {#if y0 < 0 && y1 > 0}<line x1={m.left} x2={W - m.right} y1={y(0)} y2={y(0)} stroke="var(--destructive)" stroke-dasharray="3 3" opacity="0.6" />{/if}
      {#each fc.dates as d, i (d)}
        {#if eventsByDate[d] && inView(i)}<line x1={x(i)} x2={x(i)} y1={m.top + ih} y2={m.top + ih + 5} stroke="var(--muted-foreground)" />{/if}
      {/each}
      <path d={`M${path(series)} L${x(v1)},${y(y0)} L${x(v0)},${y(y0)} Z`} fill="url(#fc-area)" />
      <path d={`M${path(series)}`} fill="none" stroke="var(--chart-1)" stroke-width="2.2" stroke-linejoin="round" />
      {#if hover != null}
        <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" />
        <circle cx={x(hover)} cy={y(series[hover])} r="5" fill="var(--chart-1)" stroke="var(--card)" stroke-width="2" />
      {/if}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation" class="cursor-crosshair"
        onmousemove={(e) => move(e.clientX)} onmouseleave={() => (pointed = null)}
        use:sideways={move} />
    </svg>
    {#if hover != null}
      <div bind:this={tipEl} class="pointer-events-none absolute z-10 min-w-44 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
        style:left={`${tipPos.left}px`} style:top={`${tipPos.top}px`}>
        <div class="text-muted-foreground">{fmtDow(fc.dates[hover])}</div>
        <div class="text-base font-semibold tabular-nums">{fmt(series[hover])}</div>
        {#each eventsByDate[fc.dates[hover]] ?? [] as ev, j (j)}
          <div class="flex justify-between gap-4"><span>{ev.name}{ev.estimated ? " (est.)" : ""}</span><span class="tabular-nums">{fmtSigned(ev.amount)}</span></div>
        {/each}
      </div>
    {/if}
  {/if}
</div>
