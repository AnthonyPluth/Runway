<script lang="ts">
  import { fmtDate, fmtDow } from "$lib/format";
  import { niceTicks, sideways } from "./numbers";
  import type { Series } from "./types";

  // A small line chart, shared by Investments and Net worth: a shared y axis, each line's name at its end, and a
  // crosshair readout of every line on the day you point at. `xs` are dates (YYYY-MM-DD), or ready-made labels
  // with `labels`. Days before `estimateUntil` are shaded: they're rebuilt from activity, not recorded. `mark` draws a
  // dashed vertical line at a (fractional) index into `xs`, with what comes after it shaded.
  let { xs, series, fmtY = String, fmtTip, height = 240, zero = false, labels = false, estimateUntil = null, table = true, mark = null }: {
    xs: string[]; series: Series[]; fmtY?: (v: number) => string; fmtTip?: (v: number) => string; height?: number;
    zero?: boolean; labels?: boolean; estimateUntil?: string | null; table?: boolean; mark?: { at: number; label: string } | null;
  } = $props();

  const COLOR = { "s-main": "var(--nw-1)", "s-alt": "var(--nw-2)", "s-muted": "var(--muted-foreground)" };
  const ft = $derived(fmtTip ?? fmtY);
  let width = $state(0);
  const H = $derived(height), m = { top: 14, right: 110, bottom: 26, left: 56 };
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = $derived(H - m.top - m.bottom);
  const n = $derived(xs.length);
  const ok = (v: number | null): v is number => v != null && isFinite(v);
  const vals = $derived(series.flatMap((s) => s.values.filter(ok)));

  const ticks = $derived.by(() => {
    if (!vals.length) return [0, 1];
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
    return niceTicks(lo, hi, 4);
  });
  const y0 = $derived(ticks[0]), y1 = $derived(ticks[ticks.length - 1]);
  const x = (i: number) => m.left + (i / Math.max(1, n - 1)) * iw;
  const y = (v: number) => m.top + (1 - (v - y0) / (y1 - y0 || 1)) * ih;
  const xLabel = (i: number) => (labels ? xs[i] : fmtDate(xs[i], n > 200 ? { month: "short", year: "2-digit" } : { month: "short", day: "numeric" }));

  // About six evenly spaced labels along the bottom.
  const xTicks = $derived.by(() => {
    const step = Math.max(1, Math.round((n - 1) / Math.max(1, Math.min(5, Math.floor(iw / 80)))));
    const out: number[] = [];
    for (let i = 0; i < n; i += step) out.push(i);
    return out;
  });

  // Each line's path; a stepped line holds its value until the next point (money deposited stays deposited).
  const paths = $derived(series.map((s) => {
    let d = "", started = false, prev: number | null = null;
    s.values.forEach((v, i) => {
      if (!ok(v)) { started = false; return; }
      if (s.step && started && prev != null) d += ` L${x(i).toFixed(1)},${y(prev).toFixed(1)}`;
      d += `${started ? " L" : " M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      started = true; prev = v;
    });
    const first = s.values.findIndex((v) => v != null), last = s.values.findLastIndex((v) => v != null);
    return { s, d, area: s.area && d ? `${d} L${x(last)},${y(y0)} L${x(first)},${y(y0)} Z` : "" };
  }));

  // Names at the line ends, nudged apart so they don't overlap (and slid back up if that pushed them off the bottom).
  const ends = $derived.by(() => {
    const out: { s: Series; xi: number; yv: number; dot: number }[] = [];
    for (const s of series) {
      let li = -1;
      s.values.forEach((v, i) => { if (v != null) li = i; });
      if (li >= 0 && ok(s.values[li])) out.push({ s, xi: x(li), yv: y(s.values[li]!), dot: y(s.values[li]!) });
    }
    out.sort((a, b) => a.yv - b.yv);
    for (let i = 1; i < out.length; i++) if (out[i].yv - out[i - 1].yv < 15) out[i].yv = out[i - 1].yv + 15;
    const over = out.length ? out[out.length - 1].yv - (m.top + ih) : 0;
    if (over > 0) out.forEach((e) => { e.yv -= over; });
    for (let i = out.length - 2; i >= 0; i--) if (out[i + 1].yv - out[i].yv < 15) out[i].yv = out[i + 1].yv - 15;
    return out;
  });

  const estX = $derived.by(() => {
    if (!estimateUntil || !n || xs[0] >= estimateUntil) return null;
    let ei = xs.findIndex((d) => d >= estimateUntil);
    if (ei < 0) ei = n - 1;
    return x(ei);
  });

  const markX = $derived(mark && n > 1 && mark.at >= 0 && mark.at <= n - 1 ? x(mark.at) : null);

  // Hovering (or dragging sideways on a phone) shows every line's value on that day.
  let pointed = $state<number | null>(null), svgEl = $state<SVGSVGElement | null>(null), tipEl = $state<HTMLDivElement | null>(null);
  // The day the readout is on, while the chart still has it (a shorter one would leave it past the end).
  const hover = $derived(pointed != null && pointed < n ? pointed : null);
  function move(clientX: number) {
    if (!svgEl) return;
    const r = svgEl.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    pointed = Math.max(0, Math.min(n - 1, Math.round(((px - m.left) / iw) * (n - 1))));
  }
  const tipLeft = $derived.by(() => {
    if (hover == null || !svgEl) return 0;
    const r = svgEl.getBoundingClientRect(), tw = tipEl?.offsetWidth ?? 160;
    return Math.min(Math.max(0, (x(hover) / W) * r.width + 12), r.width - tw);
  });

  // The same numbers as a table, for screen readers and for reading exact values: about two dozen evenly spaced rows.
  const rows = $derived.by(() => {
    const step = Math.max(1, Math.ceil(n / 24)), out: number[] = [];
    for (let i = n - 1; i >= 0; i -= step) out.push(i);
    return out.reverse();
  });
  const name = $derived(series.map((s) => s.name).join(" and "));
</script>

<div class="relative" bind:clientWidth={width}>
  {#if n < 2}
    <p class="py-6 text-center text-sm text-muted-foreground">Not enough history yet.</p>
  {:else if !vals.length}
    <p class="py-6 text-center text-sm text-muted-foreground">No data for this period yet.</p>
  {:else}
    <svg bind:this={svgEl} viewBox={`0 0 ${W} ${H}`} class="block w-full select-none text-xs" role="img" aria-label={name}>
      {#each ticks as t (t)}
        <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" />
        <text x={m.left - 8} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)">{fmtY(t)}</text>
      {/each}
      {#if zero && y0 < 0 && y1 > 0}<line x1={m.left} x2={W - m.right} y1={y(0)} y2={y(0)} stroke="var(--muted-foreground)" />{/if}
      {#each xTicks as i (i)}
        <text x={x(i)} y={H - 6} text-anchor={i === 0 ? "start" : "middle"} fill="var(--muted-foreground)">{xLabel(i)}</text>
      {/each}
      {#if estX != null}
        <rect x={m.left} y={m.top} width={Math.max(0, estX - m.left)} height={ih} fill="var(--muted-foreground)" opacity="0.08" />
        <line x1={estX} x2={estX} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" stroke-dasharray="3 3" />
        {#if estX - m.left > 70}<text x={estX - 6} y={m.top + 12} text-anchor="end" fill="var(--muted-foreground)" font-size="11">estimated</text>{/if}
      {/if}
      {#if markX != null && mark}
        <rect x={markX} y={m.top} width={Math.max(0, W - m.right - markX)} height={ih} fill="var(--muted-foreground)" opacity="0.08" data-testid="mark-shade" />
        <line x1={markX} x2={markX} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" stroke-dasharray="3 3" data-testid="mark-line" />
        <text x={markX + (W - m.right - markX > 50 ? 6 : -6)} y={m.top + 12} text-anchor={W - m.right - markX > 50 ? "start" : "end"} fill="var(--muted-foreground)" font-size="11">{mark.label}</text>
      {/if}
      {#each paths as p, k (k)}
        {#if p.area}<path d={p.area} fill={COLOR[p.s.cls]} fill-opacity="0.12" />{/if}
        <path d={p.d} fill="none" stroke={COLOR[p.s.cls]} stroke-width={p.s.cls === "s-muted" ? 1.5 : 2.2} stroke-linejoin="round" stroke-linecap="round" />
      {/each}
      {#each ends as e, k (k)}
        <circle cx={e.xi} cy={e.dot} r="4" fill={COLOR[e.s.cls]} stroke="var(--card)" stroke-width="2" />
        <text x={e.xi + 8} y={e.yv + 4} fill="var(--muted-foreground)">{e.s.name}</text>
      {/each}
      {#if hover != null}
        <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" />
        {#each series as s, k (k)}
          {@const v = s.values[hover]}
          {#if ok(v)}<circle cx={x(hover)} cy={y(v)} r="3.5" fill={COLOR[s.cls]} stroke="var(--card)" stroke-width="1.5" />{/if}
        {/each}
      {/if}
      <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation"
        onmousemove={(e) => move(e.clientX)} onmouseleave={() => (pointed = null)} use:sideways={move} />
    </svg>
    {#if hover != null}
      <div bind:this={tipEl} class="pointer-events-none absolute top-0 z-10 min-w-40 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
        style:left={`${tipLeft}px`}>
        <div class="text-muted-foreground">{labels ? xs[hover] : fmtDow(xs[hover])}{estimateUntil && xs[hover] < estimateUntil ? " · estimate" : ""}</div>
        {#each series as s, k (k)}
          {@const v = s.values[hover]}
          <div class="flex justify-between gap-4">
            <span class="flex items-center gap-1.5"><i class="inline-block h-[3px] w-2.5 rounded-sm" style:background={COLOR[s.cls]}></i>{s.name}</span>
            <span class="tabular-nums">{v == null ? "—" : ft(v)}</span>
          </div>
        {/each}
      </div>
    {/if}
    {#if table}
      <details class="mt-1">
        <summary class="cursor-pointer text-xs text-muted-foreground">Show as table</summary>
        <div class="mt-2 max-h-72 overflow-auto">
          <table class="w-full max-w-md text-sm">
            <thead><tr class="text-left text-xs text-muted-foreground">
              <th class="pb-1 font-medium">{labels ? "" : "Date"}</th>
              {#each series as s, k (k)}<th class="pb-1 text-right font-medium">{s.name}</th>{/each}
            </tr></thead>
            <tbody>
              {#each rows as i (i)}
                <tr class="border-t border-border">
                  <td class="py-1">{labels ? xs[i] : fmtDate(xs[i], { month: "short", day: "numeric", year: "numeric" })}</td>
                  {#each series as s, k (k)}<td class="py-1 text-right tabular-nums">{s.values[i] == null ? "—" : ft(s.values[i]!)}</td>{/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </details>
    {/if}
  {/if}
</div>
