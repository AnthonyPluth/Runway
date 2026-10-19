<script lang="ts">
  import { fmt, fmt0, fmtDate, fmtSigned, fmtDow, parseDate } from "$lib/format";
  import YAxis from "../YAxis.svelte";
  import { niceTicks, sideways, xScale, yScale } from "$lib/chart";
  import type { ForecastEvent, Overview } from "$lib/types";
  import { Button } from "$lib/components/ui/button";

  // The projected balance, day by day: what each day brings (hover or drag for a day's readout). The chart is one
  // focus stop, like a slider: the arrow keys step the readout a day (a tenth of the chart with Shift), Home and End go
  // to the first and last day, Escape puts it away, and "Lowest balance" puts it on the lowest day.
  // `compact` is the Overview card's version: about 120px tall, the value axis kept, the lowest day marked and the last
  // day's balance written at the line's end.
  let { fc, compact = false }: { fc: Overview; compact?: boolean } = $props();

  let width = $state(0);
  const H = $derived(compact ? 120 : 280);
  const m = $derived(compact ? { top: 20, right: 8, bottom: 22, left: 52 } : { top: 30, right: 8, bottom: 28, left: 52 });
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = $derived(H - m.top - m.bottom);
  const series = $derived(fc.total);
  const n = $derived(series.length);
  // The first day of the lowest balance (the forecast's own low), for the compact chart's mark and "Lowest balance".
  const lowAt = $derived.by(() => {
    const at = fc.low ? fc.dates.indexOf(fc.low.date) : -1;
    return at >= 0 && at < n ? at : n ? series.indexOf(Math.min(...series)) : 0;
  });

  // The days on show, v0 to v1: every day of the forecast.
  const v0 = 0, v1 = $derived(Math.max(0, n - 1));
  const inView = (i: number) => i >= v0 && i <= v1;
  const shown = (vals: number[]) => vals.slice(v0, v1 + 1);

  const ticks = $derived.by(() => {
    // The axis always starts at $0, so the line's height is the balance; it only goes lower to show
    // a balance that dips below zero.
    const lo = Math.min(0, ...shown(series)), hi = Math.max(0, ...shown(series));
    return niceTicks(lo, hi);
  });
  const scale = $derived(yScale(ticks, m.top, ih));
  const y0 = $derived(scale.y0), y1 = $derived(scale.y1);
  const x = (i: number) => xScale(v0, v1, m.left, iw)(i);
  const y = (v: number) => scale.y(v);
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
  // The day the readout is on. A refreshed, shorter forecast that no longer has that day clears it for good, so a
  // longer one coming back doesn't bring the old readout back with it.
  $effect(() => { if (pointed != null && pointed >= n) pointed = null; });
  const hover = $derived(pointed != null && pointed < n ? pointed : null);
  // On a phone the readout stays put, in the top left of the chart, instead of riding along under the finger: it's
  // readable there, and two days are compared by dragging between them. A mouse's readout follows the pointer.
  let pinned = $state(false), lastTouch = 0;
  function touched(el: SVGElement) {
    const down = () => { pinned = true; lastTouch = Date.now(); };
    el.addEventListener("touchstart", down, { passive: true });
    return { destroy() { el.removeEventListener("touchstart", down); } };
  }
  /** The day under a point on screen. */
  function dayAt(clientX: number): number {
    const r = svgEl!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    return Math.max(v0, Math.min(v1, v0 + Math.round(((px - m.left) / iw) * (v1 - v0))));
  }
  function move(clientX: number) { if (svgEl) pointed = dayAt(clientX); }
  // A tap sends a mouse move of its own right after the touch; that one doesn't unpin the readout.
  function mouse(clientX: number) { if (Date.now() - lastTouch > 1000) pinned = false; move(clientX); }

  // The keys step the readout as a slider's would; with no day chosen it starts from today.
  function key(e: KeyboardEvent) {
    const from = hover ?? 0, big = Math.max(1, Math.round(n / 10));
    const to = e.key === "ArrowLeft" || e.key === "ArrowDown" ? from - (e.shiftKey ? big : 1)
      : e.key === "ArrowRight" || e.key === "ArrowUp" ? from + (e.shiftKey ? big : 1)
      : e.key === "PageDown" ? from - big : e.key === "PageUp" ? from + big
      : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : null;
    if (e.key === "Escape" && hover != null) { e.preventDefault(); pointed = null; return; }
    if (to == null) return;
    e.preventDefault();
    pointed = Math.max(0, Math.min(n - 1, to));
  }
  // Reaching the chart with the keyboard shows today's readout at once; a click or a tap leaves the readout to the pointer.
  function focused() {
    if (hover != null) return;
    let keyboard = true;
    try { keyboard = svgEl!.matches(":focus-visible"); } catch { /* no :focus-visible (an old browser, jsdom): count it as the keyboard */ }
    if (keyboard) pointed = 0;
  }
  // "Lowest balance" puts the readout on the lowest day and the focus on the chart, to step on from there.
  function showLow(e: MouseEvent) {
    pinned = (e as PointerEvent).pointerType === "touch";
    pointed = lowAt;
    svgEl?.focus();
  }

  // What a day holds, the same for the readout and for screen readers: its events, then what the budgets take out that
  // day (in the balance, but not an event of its own).
  function dayRows(i: number) {
    const rows = (eventsByDate[fc.dates[i]] ?? []).map((ev) => ({ name: ev.name, estimated: !!ev.estimated, amount: ev.amount }));
    const spent = fc.spend?.[fc.dates[i]] ?? 0;
    if (spent > 0) rows.push({ name: "Budgeted spending", estimated: true, amount: -spent });
    return rows;
  }
  // Spoken, not laid out, so plain spaces (fmtDate's are non-breaking).
  const longDate = (i: number) => parseDate(fc.dates[i]).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  // What a screen reader hears for a day: "Monday, October 19, 2026: balance $1,300.00. Card statement (estimated) −$2,684.92."
  function spoken(i: number) {
    const rows = dayRows(i);
    const what = rows.length ? rows.map((r) => `${r.name}${r.estimated ? " (estimated)" : ""} ${fmtSigned(r.amount)}`).join("; ") : "Nothing scheduled";
    return `${longDate(i)}: balance ${fmt(series[i])}. ${what}.`;
  }
  const summary = $derived(n === 1 ? `Projected balance for today, ${fmt(series[0])}`
    : `Projected balance over the next ${n - 1} ${n === 2 ? "day" : "days"}, ${fmt(series[0])} today to ${fmt(series[v1])} on ${longDate(v1)}`);
  // The day the slider is on: the readout's, or today's while there's none.
  const at = $derived(hover ?? 0);

  const tipPos = $derived.by(() => {
    if (hover == null || !svgEl) return { left: 0, top: 0 };
    const r = svgEl.getBoundingClientRect(), tw = tipEl?.offsetWidth ?? 180;
    if (pinned) return { left: (m.left / W) * r.width, top: 0 };
    return { left: Math.min(Math.max(0, (x(hover) / W) * r.width + 12), r.width - tw), top: Math.max(0, (y(series[hover]) / H) * r.height - 60) };
  });
</script>

{#snippet row(label: string, amount: number)}
  <div class="flex items-baseline justify-between gap-4"><span class="min-w-0">{label}</span><span class="shrink-0 whitespace-nowrap tabular-nums">{fmtSigned(amount)}</span></div>
{/snippet}

<div class="relative" bind:clientWidth={width}>
  {#if !n}
    <p class="py-6 text-center text-sm text-muted-foreground">No cash accounts in the forecast yet.</p>
  {:else}
    <svg bind:this={svgEl} viewBox={`0 0 ${W} ${H}`} class="block w-full rounded-md text-xs outline-none select-none focus-visible:ring-2 focus-visible:ring-ring"
      role="slider" tabindex="0" aria-label={summary} aria-valuemin={0} aria-valuemax={n - 1} aria-valuenow={at}
      aria-valuetext={spoken(at)} onkeydown={key} onfocus={focused} onblur={() => (pointed = null)} data-testid="forecast-chart">
      <defs>
        <linearGradient id="fc-area" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="var(--chart-1)" stop-opacity="0.22" /><stop offset="1" stop-color="var(--chart-1)" stop-opacity="0" />
        </linearGradient>
      </defs>
      <YAxis {ticks} {y} left={m.left} right={W - m.right} />
      {#each months as ml (ml.i)}
        <text x={x(ml.i)} y={H - (compact ? 4 : 8)} text-anchor={ml.i === 0 ? "start" : "middle"} fill="var(--muted-foreground)">{ml.label}</text>
      {/each}
      {#if y0 < 0 && y1 > 0}<line x1={m.left} x2={W - m.right} y1={y(0)} y2={y(0)} stroke="var(--destructive)" stroke-dasharray="3 3" opacity="0.6" />{/if}
      {#each fc.dates as d, i (d)}
        {#if eventsByDate[d] && inView(i)}<line x1={x(i)} x2={x(i)} y1={m.top + ih} y2={m.top + ih + 5} stroke="var(--muted-foreground)" />{/if}
      {/each}
      <path d={`M${path(series)} L${x(v1)},${y(y0)} L${x(v0)},${y(y0)} Z`} fill="url(#fc-area)" />
      <path d={`M${path(series)}`} fill="none" stroke="var(--chart-1)" stroke-width="2.2" stroke-linejoin="round" />
      {#if compact}
        <circle cx={x(lowAt)} cy={y(series[lowAt])} r="4" fill="var(--card)" stroke="var(--chart-1)" stroke-width="2" data-low />
        <text x={x(v1)} y={Math.max(11, y(series[v1]) - 8)} text-anchor="end" fill="var(--foreground)" font-weight="600" data-end>{fmt0(series[v1])}</text>
      {/if}
      {#if hover != null}
        <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" />
        <circle cx={x(hover)} cy={y(series[hover])} r="5" fill="var(--chart-1)" stroke="var(--card)" stroke-width="2" />
      {/if}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation" class="cursor-crosshair"
        onmousemove={(e) => mouse(e.clientX)} onmouseleave={() => (pointed = null)}
        use:touched use:sideways={move} />
    </svg>
    <div class="flex justify-end">
      <Button variant="ghost" size="sm" class="h-7 px-2 text-xs text-muted-foreground" onclick={showLow} data-testid="forecast-low">Lowest balance</Button>
    </div>
    {#if hover != null}
      <!-- A row's name wraps when it's long; its amount always stays whole on one line. The same rows whether the pointer
           or the keyboard chose the day. -->
      <div bind:this={tipEl} class="pointer-events-none absolute z-10 max-w-80 min-w-44 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
        style:left={`${tipPos.left}px`} style:top={`${tipPos.top}px`} data-testid="forecast-readout">
        <div class="text-muted-foreground">{fmtDow(fc.dates[hover])}</div>
        <div class="text-base font-semibold tabular-nums">{fmt(series[hover])}</div>
        {#each dayRows(hover) as r, j (j)}
          {@render row(`${r.name}${r.estimated ? " (est.)" : ""}`, r.amount)}
        {/each}
      </div>
    {/if}
  {/if}
</div>
