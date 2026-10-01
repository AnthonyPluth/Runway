<script lang="ts">
  import { fmt0, shortMoney } from "$lib/format";
  import { niceTicks, sideways } from "./numbers";
  import type { Dollars, Projection } from "./planner";

  // The plan by age: the likely range (the middle half of the runs) shaded, the median run as a line, and
  // a marker at retirement. Ages along the bottom are the first person's; the readout shows everyone's.
  // The figures come in the dollars chosen (planner.ts projectionIn); `dollars` only says which.
  let { p, names, dollars = "today", height = 260 }: { p: Projection; names: string[]; dollars?: Dollars; height?: number } = $props();
  const unit = $derived(dollars === "future" ? "each year’s dollars" : "today’s dollars");

  let width = $state(0);
  const m = { top: 22, right: 16, bottom: 30, left: 56 };
  const W = $derived(Math.max(320, width));
  const iw = $derived(W - m.left - m.right), ih = $derived(height - m.top - m.bottom);
  const n = $derived(p.years.length);
  // An all-zero plan still gets a sensible axis ($0 to $1k), and no two ticks share a label ($1 $1 $1 $0 $0).
  const ticks = $derived.by(() => {
    const seen = new Set<string>();
    return niceTicks(0, Math.max(1000, ...p.high), 4).filter((t) => !seen.has(shortMoney(t)) && !!seen.add(shortMoney(t)));
  });
  const top = $derived(ticks[ticks.length - 1]);
  const x = (i: number) => m.left + (i / Math.max(1, n - 1)) * iw;
  const y = (v: number) => m.top + (1 - v / (top || 1)) * ih;
  const line = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const band = $derived(`${line(p.high)} ${p.low.map((_, i) => n - 1 - i).map((i) => `L${x(i).toFixed(1)},${y(p.low[i]).toFixed(1)}`).join(" ")} Z`);

  // Age labels every 5 or 10 years of the first person's age, as fit.
  const xTicks = $derived.by(() => {
    const every = iw / Math.max(1, n - 1) * 5 < 44 ? 10 : 5;
    const out: number[] = [];
    p.ages[0].forEach((a, i) => { if (a % every === 0) out.push(i); });
    return out;
  });

  let hover = $state<number | null>(null), svgEl = $state<SVGSVGElement | null>(null), tipEl = $state<HTMLDivElement | null>(null);
  function move(clientX: number) {
    if (!svgEl) return;
    const r = svgEl.getBoundingClientRect();
    hover = Math.max(0, Math.min(n - 1, Math.round(((((clientX - r.left) / r.width) * W - m.left) / iw) * (n - 1))));
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
    aria-label={`Projected investments by age, in ${unit}: median ${fmt0(p.atEnd)} at the end of the plan`}>
    {#each ticks as t (t)}
      <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" />
      <text x={m.left - 8} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)">{shortMoney(t)}</text>
    {/each}
    {#each xTicks as i (i)}
      <text x={x(i)} y={height - 10} text-anchor="middle" fill="var(--muted-foreground)">{p.ages[0][i]}</text>
    {/each}
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
    {#if hover != null}
      <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={m.top + ih} stroke="var(--muted-foreground)" />
      <circle cx={x(hover)} cy={y(p.mid[hover])} r="3.5" fill="var(--nw-1)" stroke="var(--card)" stroke-width="1.5" />
    {/if}
    <rect x={m.left} y={m.top} width={iw} height={ih} fill="transparent" role="presentation"
      onmousemove={(e) => move(e.clientX)} onmouseleave={() => (hover = null)} use:sideways={move} />
  </svg>
  <div class="-mt-1 text-center text-xs text-muted-foreground">{names[0] === "You" ? "Your age" : `${names[0]}'s age`}</div>
  {#if hover != null}
    <div bind:this={tipEl} class="pointer-events-none absolute top-0 z-10 min-w-44 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
      style:left={`${tipLeft}px`}>
      <div class="text-muted-foreground">{p.years[hover]} · {agesAt(hover)}</div>
      <div class="flex justify-between gap-4"><span>Good markets</span><span class="tabular-nums">{fmt0(p.high[hover])}</span></div>
      <div class="flex justify-between gap-4 font-medium"><span>Typical</span><span class="tabular-nums">{fmt0(p.mid[hover])}</span></div>
      <div class="flex justify-between gap-4"><span>Poor markets</span><span class="tabular-nums">{fmt0(p.low[hover])}</span></div>
    </div>
  {/if}
  <details class="mt-1">
    <summary class="cursor-pointer text-xs text-muted-foreground">Show as table</summary>
    <div class="mt-2 max-h-72 overflow-auto">
      <table class="w-full max-w-lg text-sm">
        <caption class="pb-1 text-left text-xs text-muted-foreground">In {unit}</caption>
        <thead><tr class="text-left text-xs text-muted-foreground">
          <th class="pb-1 font-medium">Year</th><th class="pb-1 font-medium">Age</th>
          <th class="pb-1 text-right font-medium">Poor markets</th><th class="pb-1 text-right font-medium">Typical</th>
          <th class="pb-1 text-right font-medium">Good markets</th>
        </tr></thead>
        <tbody>
          {#each p.years as yr, i (yr)}
            <tr class="border-t border-border">
              <td class="py-1">{yr}</td><td class="py-1">{p.ages.map((a) => a[i]).join(" / ")}</td>
              <td class="py-1 text-right tabular-nums">{fmt0(p.low[i])}</td>
              <td class="py-1 text-right tabular-nums">{fmt0(p.mid[i])}</td>
              <td class="py-1 text-right tabular-nums">{fmt0(p.high[i])}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
</div>
