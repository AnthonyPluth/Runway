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

  // Zoom: the days on show, v0 to v1 (every day unless you've dragged across part of the chart). A new forecast
  // (another length, a change) shows every day again.
  let view = $state<[number, number] | null>(null);
  $effect(() => { void fc; view = null; });
  const v0 = $derived(view ? view[0] : 0), v1 = $derived(view ? view[1] : Math.max(0, n - 1));
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
    const both = alt ? shown(series).concat(shown(alt)) : shown(series);
    // The axis always starts at $0 (zoomed in too), so the line's height is the balance; it only goes lower to show
    // a balance that dips below zero.
    const lo = Math.min(0, ...both), hi = Math.max(0, ...both);
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
  // Labels along the bottom: the first of each month, or (zoomed in to a few weeks) evenly spaced days.
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
  // From the first estimated card payment on, the line includes spending that hasn't happened yet.
  const estIndex = $derived.by(() => {
    const first = fc.events.filter((e) => e.kind === "card" && e.estimated).map((e) => e.date).sort()[0];
    return first ? fc.dates.indexOf(first) : -1;
  });
  const lowIndex = $derived(fc.dates.indexOf(fc.low.date));
  const lowColor = $derived(lowIndex >= 0 && series[lowIndex] < 0 ? "var(--destructive)" : "var(--foreground)");

  // Labels get a plate behind them so the line doesn't run through; the "ends at" note moves (or goes) when it
  // would sit on the low-point label.
  let lowLabel = $state<SVGTextElement | null>(null), endNote = $state<SVGTextElement | null>(null);
  let lowBox = $state<DOMRect | null>(null), endBox = $state<DOMRect | null>(null), endY = $state(0), showEnd = $state(true);
  const endYDefault = $derived.by(() => { const ey = y(series[v1]); return ey < m.top + 20 ? ey + 18 : ey - 10; });
  $effect(() => {
    void W; void fc;   // re-measure when the chart is redrawn
    void v0; void v1;
    endY = endYDefault; showEnd = !inView(lowIndex) || lowIndex < v1 - 4;
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
  // Touching and holding still, then dragging, selects days to zoom into instead.
  function sideways(el: SVGElement, onMove: (clientX: number) => void) {
    let start: Touch | null = null, hold: ReturnType<typeof setTimeout> | undefined, selecting = false;
    const down = (e: TouchEvent) => {
      start = e.touches[0]; selecting = false; clearTimeout(hold);
      const at = start.clientX;
      hold = setTimeout(() => { selecting = true; startBrush(at); navigator.vibrate?.(10); }, 350);
    };
    const drag = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!selecting && start && Math.hypot(t.clientX - start.clientX, t.clientY - start.clientY) > 8) clearTimeout(hold);
      if (selecting) { moveBrush(t.clientX); if (e.cancelable) e.preventDefault(); return; }
      if (start && Math.abs(t.clientY - start.clientY) > Math.abs(t.clientX - start.clientX)) return;
      onMove(t.clientX);
      if (e.cancelable) e.preventDefault();
    };
    const up = () => { clearTimeout(hold); if (selecting) { selecting = false; endBrush(); } };
    el.addEventListener("touchstart", down, { passive: true });
    el.addEventListener("touchmove", drag, { passive: false });
    el.addEventListener("touchend", up);
    el.addEventListener("touchcancel", up);
    return { destroy() {
      clearTimeout(hold);
      el.removeEventListener("touchstart", down); el.removeEventListener("touchmove", drag);
      el.removeEventListener("touchend", up); el.removeEventListener("touchcancel", up);
    } };
  }
  /** The day under a point on screen. */
  function dayAt(clientX: number): number {
    const r = svgEl!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    return Math.max(v0, Math.min(v1, v0 + Math.round(((px - m.left) / iw) * (v1 - v0))));
  }
  function move(clientX: number) { if (svgEl) hover = dayAt(clientX); }

  // Dragging across the chart (with the mouse, or after a touch and hold) picks the days to zoom into.
  let brush = $state<[number, number] | null>(null);
  function startBrush(clientX: number) { if (svgEl) { const d = dayAt(clientX); brush = [d, d]; hover = null; } }
  function moveBrush(clientX: number) { if (brush && svgEl) brush = [brush[0], dayAt(clientX)]; }
  function endBrush() {
    if (!brush) return;
    const [a, b] = [Math.min(...brush), Math.max(...brush)];
    brush = null;
    if (b - a >= 2) { view = [a, b]; hover = null; }   // a few days at least; a click is just a click
  }
  const zoomLabel = $derived(view ? `${fmtDate(fc.dates[v0])} – ${fmtDate(fc.dates[v1])}` : "");
  const tipPos = $derived.by(() => {
    if (hover == null || !svgEl) return { left: 0, top: 0 };
    const r = svgEl.getBoundingClientRect(), tw = tipEl?.offsetWidth ?? 180;
    return { left: Math.min(Math.max(0, (x(hover) / W) * r.width + 12), r.width - tw), top: Math.max(0, (y(series[hover]) / H) * r.height - 60) };
  });
</script>

<svelte:window onmouseup={endBrush} onkeydown={(e) => { if (e.key === "Escape" && view) view = null; }} />

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
      {#if alt}<path d={`M${path(alt)}`} fill="none" stroke="var(--chart-2)" stroke-width="1.6" stroke-dasharray="5 4" />{/if}
      <path d={`M${path(series)}`} fill="none" stroke="var(--chart-1)" stroke-width="2.2" stroke-linejoin="round" />
      {#if estIndex > v0 && estIndex <= v1}
        {@const ex = x(estIndex)}
        <line x1={ex} x2={ex} y1={m.top - 6} y2={m.top + ih} stroke="var(--muted-foreground)" stroke-dasharray="2 3" />
      {/if}
      {#if lowIndex >= 0 && inView(lowIndex)}
        {@const lx = x(lowIndex)}
        {@const ly = y(series[lowIndex])}
        {@const anchor = lx > W - 140 ? "end" : lx < m.left + 80 ? "start" : "middle"}
        <circle cx={lx} cy={ly} r="5.5" fill={lowColor} stroke="var(--card)" stroke-width="2" />
        {#if lowBox}<rect x={lowBox.x - 7} y={lowBox.y - 4} width={lowBox.width + 14} height={lowBox.height + 8} rx="6" fill="var(--popover)" />{/if}
        <text bind:this={lowLabel} x={lx + (anchor === "start" ? 10 : anchor === "end" ? -10 : 0)} y={ly + (ly > m.top + ih - 30 ? -14 : 24)}
          text-anchor={anchor} fill={lowColor} font-weight="600">Low {fmt0(series[lowIndex])} · {fmtDate(fc.low.date)}</text>
      {/if}
      {#if showEnd}
        {#if endBox}<rect x={endBox.x - 6} y={endBox.y - 3} width={endBox.width + 12} height={endBox.height + 6} rx="5" fill="var(--card)" />{/if}
        <text bind:this={endNote} x={x(v1)} y={endY} text-anchor="end" fill="var(--muted-foreground)">
          {fmt0(series[v1])} by {fmtDate(fc.dates[v1])}
        </text>
      {/if}
      {#if hover != null}
        <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" />
        <circle cx={x(hover)} cy={y(series[hover])} r="5" fill="var(--chart-1)" stroke="var(--card)" stroke-width="2" />
      {/if}
      {#if brush}
        {@const bx = x(Math.min(...brush))}
        <rect x={bx} y={m.top} width={Math.max(1, x(Math.max(...brush)) - bx)} height={ih} fill="var(--chart-1)" opacity="0.18" />
      {/if}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation" class={brush ? "cursor-ew-resize" : "cursor-crosshair"}
        onmousedown={(e) => { if (e.button === 0) { e.preventDefault(); startBrush(e.clientX); } }}
        onmousemove={(e) => { if (brush) moveBrush(e.clientX); else move(e.clientX); }} onmouseleave={() => (hover = null)}
        ondblclick={() => (view = null)}
        use:sideways={move} />
    </svg>
    <div class="mt-1 flex min-h-7 items-center justify-between gap-3 text-xs text-muted-foreground">
      {#if view}
        <span class="tabular-nums">Showing {zoomLabel}</span>
        <button type="button" class="cursor-pointer rounded-full bg-muted px-3 py-1 font-medium text-foreground hover:bg-accent" onclick={() => (view = null)}>Reset zoom</button>
      {:else}
        <span><span class="[@media(hover:none)]:hidden">Drag across the chart to zoom in</span><span class="hidden [@media(hover:none)]:inline">Touch and hold, then drag, to zoom in</span></span>
      {/if}
    </div>
    {#if hover != null}
      <div bind:this={tipEl} class="pointer-events-none absolute z-10 min-w-44 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
        style:left={`${tipPos.left}px`} style:top={`${tipPos.top}px`}>
        <div class="text-muted-foreground">{fmtDow(fc.dates[hover])}</div>
        <div class="text-base font-semibold tabular-nums">{fmt(series[hover])}</div>
        {#if alt}<div class="flex justify-between gap-4" style:color="var(--chart-2)"><span>If you stick to your budget</span><span class="tabular-nums">{fmt(alt[hover])}</span></div>{/if}
        {#each eventsByDate[fc.dates[hover]] ?? [] as ev, j (j)}
          <div class="flex justify-between gap-4"><span>{ev.name}{ev.estimated ? " (est.)" : ""}</span><span class="tabular-nums">{fmt(ev.amount)}</span></div>
        {/each}
      </div>
    {/if}
  {/if}
</div>
