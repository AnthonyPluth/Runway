<script lang="ts">
  import { fmt0, shortMoney } from "$lib/format";
  import { niceTicks, sideways, xScale, yScale } from "$lib/chart";
  import YAxis from "../YAxis.svelte";
  import { HELD_KINDS, type Dollars, type HeldKind, type Projection } from "./planner";

  // The plan by age: the likely range (the middle half of the runs) shaded, the median run as a line, a marker at
  // today (where it starts) and one at retirement. Along the bottom, the first person's ages with the calendar year
  // under each; the readout shows everyone's.
  // What's held outside the investments (home equity, other assets, vested equity, until each is sold into the plan)
  // is stacked on top of the typical run, so the top edge is what you'd have in all.
  // The figures come in the dollars chosen (planner.ts projectionIn); `dollars` only says which.
  let { p, names, dollars = "today", height = 260 }: { p: Projection; names: string[]; dollars?: Dollars; height?: number } = $props();
  const unit = $derived(dollars === "future" ? "each year’s dollars" : "today’s dollars");

  const HELD: Record<HeldKind, { label: string; color: string }> = {
    home: { label: "Home equity", color: "var(--nw-2)" },
    other: { label: "Other assets", color: "var(--nw-6)" },
    equity: { label: "Equity (vested)", color: "var(--nw-4)" },
  };
  // The kinds with anything in them, bottom to top, each with its layer's lower and upper edge.
  const layers = $derived.by(() => {
    let base = p.mid;
    const out: { kind: HeldKind; lo: number[]; hi: number[] }[] = [];
    for (const kind of HELD_KINDS) {
      const vals = p.held?.[kind];
      if (!vals?.some((v) => v > 0)) continue;
      const hi = base.map((b, i) => b + vals[i]);
      out.push({ kind, lo: base, hi });
      base = hi;
    }
    return out;
  });
  const total = $derived(layers.length ? layers[layers.length - 1].hi : p.mid);

  let width = $state(0);
  const m = { top: 22, right: 16, bottom: 42, left: 56 };
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = $derived(height - m.top - m.bottom);
  const n = $derived(p.years.length);
  // An all-zero plan still gets a sensible axis ($0 to $1k), and no two ticks share a label ($1 $1 $1 $0 $0).
  const ticks = $derived.by(() => {
    const seen = new Set<string>();
    return niceTicks(0, Math.max(1000, ...p.high, ...total), 4).filter((t) => !seen.has(shortMoney(t)) && !!seen.add(shortMoney(t)));
  });
  const scale = $derived(yScale(ticks, m.top, ih));
  const x = (i: number) => xScale(0, n - 1, m.left, iw)(i);
  const y = (v: number) => scale.y(v);
  const line = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = (hi: number[], lo: number[]) => `${line(hi)} ${lo.map((_, i) => n - 1 - i).map((i) => `L${x(i).toFixed(1)},${y(lo[i]).toFixed(1)}`).join(" ")} Z`;
  const band = $derived(area(p.high, p.low));

  // Age labels every 5 or 10 years of the first person's age, as fit.
  const xTicks = $derived.by(() => {
    const every = iw / Math.max(1, n - 1) * 5 < 44 ? 10 : 5;
    const out: number[] = [];
    p.ages[0].forEach((a, i) => { if (a % every === 0) out.push(i); });
    return out;
  });

  let pointed = $state<number | null>(null), svgEl = $state<SVGSVGElement | null>(null), tipEl = $state<HTMLDivElement | null>(null);
  // The year the readout is on, while the chart still has it (a shorter one would leave it past the end).
  const hover = $derived(pointed != null && pointed < n ? pointed : null);
  function move(clientX: number) {
    if (!svgEl) return;
    const r = svgEl.getBoundingClientRect();
    pointed = Math.max(0, Math.min(n - 1, Math.round(((((clientX - r.left) / r.width) * W - m.left) / iw) * (n - 1))));
  }
  const tipLeft = $derived.by(() => {
    if (hover == null || !svgEl) return 0;
    const r = svgEl.getBoundingClientRect(), tw = tipEl?.offsetWidth ?? 180;
    return Math.min(Math.max(0, (x(hover) / W) * r.width + 12), r.width - tw);
  });
  const agesAt = (i: number) => names.map((nm, k) => `${nm} ${p.ages[k][i]}`).join(" · ");
</script>

<div class="relative" bind:clientWidth={width}>
  <svg bind:this={svgEl} viewBox={`0 0 ${W} ${height}`} class="block w-full select-none text-xs" role="img"
    aria-label={`Projected investments by age, in ${unit}: median ${fmt0(p.atEnd)} at the end of the plan${
      layers.length ? `, ${fmt0(total[n - 1])} with ${layers.map((l) => HELD[l.kind].label.toLowerCase()).join(" and ")}` : ""}`}>
    <YAxis {ticks} {y} left={m.left} right={W - m.right} />
    {#each xTicks as i (i)}
      <text x={x(i)} y={height - 24} text-anchor="middle" fill="var(--muted-foreground)">{p.ages[0][i]}</text>
      <text x={x(i)} y={height - 10} text-anchor="middle" fill="var(--muted-foreground)" font-size="10" opacity="0.8" data-year>{p.years[i]}</text>
    {/each}
    {#each layers as l (l.kind)}
      <path d={area(l.hi, l.lo)} fill={HELD[l.kind].color} fill-opacity="0.35" data-layer={l.kind} />
    {/each}
    {#if layers.length}<path d={line(total)} fill="none" stroke="var(--muted-foreground)" stroke-width="1.2" />{/if}
    <path d={band} fill="var(--nw-1)" fill-opacity="0.16" />
    <path d={line(p.high)} fill="none" stroke="var(--nw-1)" stroke-opacity="0.5" stroke-dasharray="2 3" />
    <path d={line(p.low)} fill="none" stroke="var(--nw-1)" stroke-opacity="0.5" stroke-dasharray="2 3" />
    <path d={line(p.mid)} fill="none" stroke="var(--nw-1)" stroke-width="2.2" stroke-linejoin="round" />
    {#if p.retireIndex > 0}
      {@const rx = x(p.retireIndex)}
      <line x1={rx} x2={rx} y1={m.top} y2={m.top + ih} stroke="var(--nw-3)" stroke-width="2" />
      <circle cx={rx} cy={m.top} r="4" fill="var(--nw-3)" />
      <text x={rx > W - 110 ? rx - 8 : rx + 8} y={m.top + 4} text-anchor={rx > W - 110 ? "end" : "start"} fill="var(--foreground)" font-weight="500">retirement</text>
    {/if}
    <g data-testid="today-mark">
      <circle cx={x(0)} cy={y(p.mid[0])} r="4" fill="var(--foreground)" stroke="var(--card)" stroke-width="2" />
      <text x={x(0) + 8} y={y(p.mid[0]) - 8} fill="var(--foreground)" font-weight="500">today</text>
    </g>
    {#if hover != null}
      <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" />
      <circle cx={x(hover)} cy={y(p.mid[hover])} r="3.5" fill="var(--nw-1)" stroke="var(--card)" stroke-width="1.5" />
    {/if}
    <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation"
      onmousemove={(e) => move(e.clientX)} onmouseleave={() => (pointed = null)} use:sideways={move} />
  </svg>
  <div class="-mt-1 text-center text-xs text-muted-foreground">{names[0] === "You" ? "Your age" : `${names[0]}'s age`} · year</div>
  {#if hover != null}
    <div bind:this={tipEl} class="pointer-events-none absolute top-0 z-10 min-w-44 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
      style:left={`${tipLeft}px`}>
      <div class="text-muted-foreground">{p.years[hover]} · {agesAt(hover)}</div>
      <div class="flex justify-between gap-4"><span>Good markets</span><span class="tabular-nums">{fmt0(p.high[hover])}</span></div>
      <div class="flex justify-between gap-4 font-medium"><span>Typical</span><span class="tabular-nums">{fmt0(p.mid[hover])}</span></div>
      <div class="flex justify-between gap-4"><span>Poor markets</span><span class="tabular-nums">{fmt0(p.low[hover])}</span></div>
      {#if layers.length}
        <div class="mt-1 border-t pt-1"></div>
        {#each layers as l (l.kind)}
          <div class="flex justify-between gap-4"><span>{HELD[l.kind].label}</span><span class="tabular-nums">{fmt0(p.held[l.kind][hover])}</span></div>
        {/each}
        <div class="flex justify-between gap-4 font-medium"><span>Typical, in all</span><span class="tabular-nums">{fmt0(total[hover])}</span></div>
      {/if}
    </div>
  {/if}
  {#if layers.length}
    <ul class="mt-1 flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Chart key">
      <li class="inline-flex items-center gap-1.5"><i class="inline-block h-0.5 w-3 bg-[var(--nw-1)]"></i>Investments (typical, likely range)</li>
      {#each layers as l (l.kind)}
        <li class="inline-flex items-center gap-1.5"><i class="inline-block size-2.5 rounded-[3px] opacity-60" style:background={HELD[l.kind].color}></i>{HELD[l.kind].label}</li>
      {/each}
    </ul>
  {/if}
  <details class="mt-1">
    <summary class="cursor-pointer text-xs text-muted-foreground">Show as table</summary>
    <div class="mt-2 max-h-72 overflow-auto">
      <table class={`w-full text-sm ${layers.length ? "max-w-3xl" : "max-w-lg"}`}>
        <caption class="pb-1 text-left text-xs text-muted-foreground">In {unit}</caption>
        <thead><tr class="text-left text-xs text-muted-foreground">
          <th class="pb-1 font-medium">Year</th><th class="pb-1 font-medium">Age</th>
          <th class="pb-1 text-right font-medium">Poor markets</th><th class="pb-1 text-right font-medium">Typical</th>
          <th class="pb-1 text-right font-medium">Good markets</th>
          {#each layers as l (l.kind)}<th class="pb-1 text-right font-medium">{HELD[l.kind].label}</th>{/each}
        </tr></thead>
        <tbody>
          {#each p.years as yr, i (yr)}
            <tr class="border-t border-border">
              <td class="py-1">{yr}</td><td class="py-1">{p.ages.map((a) => a[i]).join(" / ")}</td>
              <td class="py-1 text-right tabular-nums">{fmt0(p.low[i])}</td>
              <td class="py-1 text-right tabular-nums">{fmt0(p.mid[i])}</td>
              <td class="py-1 text-right tabular-nums">{fmt0(p.high[i])}</td>
              {#each layers as l (l.kind)}<td class="py-1 text-right tabular-nums">{fmt0(p.held[l.kind][i])}</td>{/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
</div>
