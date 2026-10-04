<svelte:options namespace="svg" />
<script lang="ts">
  import { shortMoney } from "$lib/format";

  // The gridlines and value labels down a chart's left edge: one line across from `left` to `right` per tick, `y(t)`
  // its row, and its label (`fmt`, dollars by default) right-aligned `gap` px before `left`.
  let { ticks, y, left, right, fmt = shortMoney, gap = 8, fontSize, textClass, crisp = false }: {
    ticks: number[]; y: (v: number) => number; left: number; right: number; fmt?: (v: number) => string; gap?: number;
    fontSize?: string; textClass?: string; crisp?: boolean;
  } = $props();
</script>

{#each ticks as t (t)}
  <line x1={left} x2={right} y1={y(t)} y2={y(t)} stroke="var(--border)" shape-rendering={crisp ? "crispEdges" : undefined} />
  <text x={left - gap} y={y(t) + 4} text-anchor="end" fill="var(--muted-foreground)" font-size={fontSize} class={textClass}>{fmt(t)}</text>
{/each}
