<script lang="ts">
  import { fmt, shortMoney } from "$lib/format";
  import { niceTicks } from "./numbers";

  // A single-series column chart (dividends and interest by month), with the month's amount on hover or tap.
  let { labels, values, fmtTip = fmt, height = 200, label = "Monthly dividends and interest" }: {
    labels: string[]; values: number[]; fmtTip?: (v: number) => string; height?: number; label?: string;
  } = $props();

  let width = $state(0);
  const H = $derived(height), m = { top: 12, right: 8, bottom: 24, left: 48 };
  const W = $derived(Math.max(300, width));
  const iw = $derived(W - m.left - m.right), ih = $derived(H - m.top - m.bottom);
  const hi = $derived(Math.max(...values, 0));
  const ticks = $derived(niceTicks(0, hi || 1, 3));
  const y1 = $derived(ticks[ticks.length - 1]);
  const bw = $derived(iw / Math.max(1, values.length)), w = $derived(Math.min(24, bw - 2));
  const y = (v: number) => m.top + (1 - v / y1) * ih;
  const every = $derived(Math.ceil(values.length / 8));
  // A column with rounded top corners.
  function bar(i: number, v: number): string {
    const cx = m.left + bw * i + bw / 2, top = y(v), h = m.top + ih - top, r = Math.min(4, h), l = cx - w / 2, rt = cx + w / 2;
    return `M${l},${m.top + ih} V${top + r} Q${l},${top} ${l + r},${top} H${rt - r} Q${rt},${top} ${rt},${top + r} V${m.top + ih} Z`;
  }

  let hover = $state<number | null>(null), box = $state<HTMLDivElement | null>(null), tipEl = $state<HTMLDivElement | null>(null);
  let pointerX = $state(0);
  function point(e: PointerEvent, i: number) {
    hover = i;
    if (box) pointerX = e.clientX - box.getBoundingClientRect().left;
  }
  const tipLeft = $derived(Math.max(0, Math.min(pointerX + 10, width - (tipEl?.offsetWidth ?? 100))));
</script>

<div class="relative" bind:this={box} bind:clientWidth={width}>
  {#if hi <= 0}
    <p class="py-6 text-center text-sm text-muted-foreground">No dividends or interest recorded yet.</p>
  {:else}
    <svg viewBox={`0 0 ${W} ${H}`} class="block w-full select-none text-xs" role="img" aria-label={label}>
      {#each ticks as t (t)}
        <line x1={m.left} x2={W - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" />
        <text x={m.left - 6} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)">{shortMoney(t)}</text>
      {/each}
      {#each values as v, i (i)}
        {#if hover === i}<rect x={m.left + bw * i} y={m.top} width={bw} height={ih} fill="var(--foreground)" opacity="0.04" />{/if}
        {#if v > 0}<path d={bar(i, v)} fill="var(--nw-1)" />{/if}
        {#if i % every === 0}<text x={m.left + bw * i + bw / 2} y={H - 6} text-anchor="middle" fill="var(--muted-foreground)">{labels[i]}</text>{/if}
        <rect x={m.left + bw * i} y={m.top} width={bw} height={ih} fill="transparent" class="cursor-pointer" role="presentation"
          onpointermove={(e) => point(e, i)} onpointerdown={(e) => point(e, i)} onpointerleave={() => (hover = null)} />
      {/each}
    </svg>
    {#if hover != null}
      <div bind:this={tipEl} class="pointer-events-none absolute top-0 z-10 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
        style:left={`${tipLeft}px`}>
        <div class="text-muted-foreground">{labels[hover]}</div>
        <div class="text-base font-semibold tabular-nums">{fmtTip(values[hover])}</div>
      </div>
    {/if}
    <details class="mt-1">
      <summary class="cursor-pointer text-xs text-muted-foreground">Show as table</summary>
      <table class="mt-2 w-full max-w-xs text-sm">
        <thead><tr class="text-left text-xs text-muted-foreground"><th class="pb-1 font-medium">Month</th><th class="pb-1 text-right font-medium">Amount</th></tr></thead>
        <tbody>
          {#each values as v, i (i)}
            <tr class="border-t border-border"><td class="py-1">{labels[i]}</td><td class="py-1 text-right tabular-nums">{fmtTip(v)}</td></tr>
          {/each}
        </tbody>
      </table>
    </details>
  {/if}
</div>
