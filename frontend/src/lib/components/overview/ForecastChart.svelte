<script lang="ts">
  import { fmt, fmt0, fmtDate, fmtDow, parseDate, shortMoney } from "$lib/format";
  import type { ForecastEvent, Overview } from "$lib/types";

  // The projected balance, day by day: the low point, where estimated spending starts, and what each day brings.
  let { fc }: { fc: Overview } = $props();

  let width = $state(0);
  const H = 280, m = { top: 30, right: 8, bottom: 28, left: 52 };
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = H - m.top - m.bottom;
  const series = $derived(fc.total);
  const n = $derived(series.length);
  const alt = $derived(fc.budget?.total?.length === series.length ? fc.budget.total : null);

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
    const both = alt ? series.concat(alt) : series;
    let lo = Math.min(...both);
    const hi = Math.max(...both);
    if (lo > 0 && lo < hi * 0.25) lo = 0;   // near zero: show the floor
    return niceTicks(lo, hi);
  });
  const y0 = $derived(ticks[0]), y1 = $derived(ticks[ticks.length - 1]);
  const x = (i: number) => m.left + (i / Math.max(1, n - 1)) * iw;
  const y = (v: number) => m.top + (1 - (v - y0) / (y1 - y0 || 1)) * ih;
  const path = (vals: number[]) => vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" L");

  const eventsByDate = $derived.by(() => {
    const by: Record<string, ForecastEvent[]> = {};
    for (const e of fc.events) (by[e.date] ||= []).push(e);
    return by;
  });
  // Month labels along the bottom.
  const months = $derived(fc.dates.flatMap((d, i) => {
    const dt = parseDate(d);
    if (i !== 0 && dt.getDate() !== 1) return [];
    if (i !== 0 && x(i) - m.left <= 56) return [];
    return [{ i, label: i === 0 ? "Today" : dt.toLocaleDateString("en-US", { month: "short" }) }];
  }));
  // From the first estimated card payment on, the line includes spending that hasn't happened yet.
  const estIndex = $derived.by(() => {
    const first = fc.events.filter((e) => e.kind === "card" && e.estimated).map((e) => e.date).sort()[0];
    return first ? fc.dates.indexOf(first) : -1;
  });
  const lowIndex = $derived(fc.dates.indexOf(fc.low.date));

  // Labels get a plate behind them so the line doesn't run through; the "ends at" note moves (or goes) when it
  // would sit on the low-point label.
  let lowLabel = $state<SVGTextElement | null>(null), endNote = $state<SVGTextElement | null>(null);
  let lowBox = $state<DOMRect | null>(null), endBox = $state<DOMRect | null>(null), endY = $state(0), showEnd = $state(true);
  const endYDefault = $derived.by(() => { const ey = y(series[n - 1]); return ey < m.top + 20 ? ey + 18 : ey - 10; });
  $effect(() => {
    void W; void fc;   // re-measure when the chart is redrawn
    endY = endYDefault; showEnd = lowIndex < n - 4;
    queueMicrotask(() => {
      lowBox = lowLabel?.getBBox() ?? null;
      if (!endNote || !showEnd) { endBox = null; return; }
      let b = endNote.getBBox();
      if (lowBox) {
        const lo = { x: lowBox.x - 7, y: lowBox.y - 4, w: lowBox.width + 14, h: lowBox.height + 8 };
        const overlaps = (r: DOMRect) => r.x < lo.x + lo.w + 4 && r.x + r.width > lo.x - 4 && r.y < lo.y + lo.h + 4 && r.y + r.height > lo.y - 4;
        if (overlaps(b)) {
          const moved = lo.y - 8 > m.top + 10 ? lo.y - 8 : lo.y + lo.h + 16;
          b = new DOMRect(b.x, b.y + (moved - endY), b.width, b.height);
          endY = moved;
          if (overlaps(b)) { showEnd = false; endBox = null; return; }
        }
      }
      endBox = b;
    });
  });

  // Hovering (or dragging sideways on a phone) shows that day's balance and what happens on it.
  let hover = $state<number | null>(null), svgEl = $state<SVGSVGElement | null>(null), tipEl = $state<HTMLDivElement | null>(null);
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
  function move(clientX: number) {
    if (!svgEl) return;
    const r = svgEl.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    hover = Math.max(0, Math.min(n - 1, Math.round(((px - m.left) / iw) * (n - 1))));
  }
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
    <svg bind:this={svgEl} viewBox={`0 0 ${W} ${H}`} class="block w-full select-none text-[11.5px]" role="img"
      aria-label={`Projected balance over the next ${n - 1} days`}>
      <defs>
        <linearGradient id="fc-area" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="var(--primary)" stop-opacity="0.22" /><stop offset="1" stop-color="var(--primary)" stop-opacity="0" />
        </linearGradient>
      </defs>
      {#each ticks as t (t)}
        <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
        <text x={m.left - 8} y={y(t) + 4} text-anchor="end" fill="var(--faint-foreground)">{shortMoney(t)}</text>
      {/each}
      {#each months as ml (ml.i)}
        <text x={x(ml.i)} y={H - 8} text-anchor={ml.i === 0 ? "start" : "middle"} fill="var(--faint-foreground)">{ml.label}</text>
      {/each}
      {#if y0 < 0 && y1 > 0}<line x1={m.left} x2={W - m.right} y1={y(0)} y2={y(0)} stroke="var(--destructive)" stroke-dasharray="3 3" opacity="0.6" />{/if}
      {#each fc.dates as d, i (d)}
        {#if eventsByDate[d]}<line x1={x(i)} x2={x(i)} y1={m.top + ih} y2={m.top + ih + 5} stroke="var(--faint-foreground)" />{/if}
      {/each}
      <path d={`M${path(series)} L${x(n - 1)},${y(y0)} L${x(0)},${y(y0)} Z`} fill="url(#fc-area)" />
      {#if alt}<path d={`M${path(alt)}`} fill="none" stroke="var(--good)" stroke-width="1.6" stroke-dasharray="5 4" />{/if}
      <path d={`M${path(series)}`} fill="none" stroke="var(--primary)" stroke-width="2.2" stroke-linejoin="round" />
      {#if estIndex > 0}
        {@const ex = x(estIndex)}
        {@const right = ex < W - m.right - 150}
        <line x1={ex} x2={ex} y1={m.top - 6} y2={m.top + ih} stroke="var(--faint-foreground)" stroke-dasharray="2 3" />
        <text x={ex + (right ? 6 : -6)} y={m.top - 10} text-anchor={right ? "start" : "end"} fill="var(--muted-foreground)">
          {right ? "Estimated new spending from here →" : "← Estimated new spending from here"}
        </text>
      {/if}
      {#if lowIndex >= 0}
        {@const lx = x(lowIndex)}
        {@const ly = y(series[lowIndex])}
        {@const anchor = lx > W - 140 ? "end" : lx < m.left + 80 ? "start" : "middle"}
        <circle cx={lx} cy={ly} r="5.5" fill="var(--low)" stroke="var(--card)" stroke-width="2" />
        {#if lowBox}<rect x={lowBox.x - 7} y={lowBox.y - 4} width={lowBox.width + 14} height={lowBox.height + 8} rx="6" fill="var(--popover)" />{/if}
        <text bind:this={lowLabel} x={lx + (anchor === "start" ? 10 : anchor === "end" ? -10 : 0)} y={ly + (ly > m.top + ih - 30 ? -14 : 24)}
          text-anchor={anchor} fill="var(--low)" font-weight="600">Low {fmt0(series[lowIndex])} · {fmtDate(fc.low.date)}</text>
      {/if}
      {#if showEnd}
        {#if endBox}<rect x={endBox.x - 6} y={endBox.y - 3} width={endBox.width + 12} height={endBox.height + 6} rx="5" fill="var(--card)" />{/if}
        <text bind:this={endNote} x={x(n - 1)} y={endY} text-anchor="end" fill="var(--subtle-foreground)">
          {fmt0(series[n - 1])} by {fmtDate(fc.dates[n - 1])}
        </text>
      {/if}
      {#if hover != null}
        <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--faint-foreground)" />
        <circle cx={x(hover)} cy={y(series[hover])} r="5" fill="var(--primary)" stroke="var(--card)" stroke-width="2" />
      {/if}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation"
        onmousemove={(e) => move(e.clientX)} onmouseleave={() => (hover = null)}
        use:sideways={move} />
    </svg>
    {#if hover != null}
      <div bind:this={tipEl} class="pointer-events-none absolute z-10 min-w-44 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
        style:left={`${tipPos.left}px`} style:top={`${tipPos.top}px`}>
        <div class="text-muted-foreground">{fmtDow(fc.dates[hover])}</div>
        <div class="text-base font-semibold text-foreground-strong tabular">{fmt(series[hover])}</div>
        {#if alt}<div class="flex justify-between gap-4 text-good"><span>If you stick to your budget</span><span class="tabular">{fmt(alt[hover])}</span></div>{/if}
        {#each eventsByDate[fc.dates[hover]] ?? [] as ev, j (j)}
          <div class="flex justify-between gap-4"><span>{ev.name}{ev.estimated ? " (est.)" : ""}</span><span class="tabular">{fmt(ev.amount)}</span></div>
        {/each}
      </div>
    {/if}
  {/if}
</div>
