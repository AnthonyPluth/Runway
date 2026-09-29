<script lang="ts">
  import type { Snippet } from "svelte";

  // A chart's readout, kept inside the chart's box. `x`/`y` are where the pointer is, relative to that box.
  // "top" sits at the top of the box beside the pointer (bar charts), "follow" below and right of it (treemap),
  // "above" just above and right of it (Sankey).
  let { x, y = 0, boxWidth, boxHeight = 0, place = "top", children }: {
    x: number; y?: number; boxWidth: number; boxHeight?: number; place?: "top" | "follow" | "above"; children: Snippet;
  } = $props();
  let w = $state(0), h = $state(0);
  const left = $derived(Math.max(0, Math.min(x + (place === "above" ? 14 : 12), boxWidth - w - (place === "above" ? 4 : 0))));
  const top = $derived(place === "top" ? 0 : place === "above" ? Math.max(0, y - 20) : Math.max(0, Math.min(y + 12, boxHeight - h)));
</script>

<div bind:offsetWidth={w} bind:offsetHeight={h}
  class="pointer-events-none absolute z-10 min-w-40 max-w-72 rounded-lg bg-popover px-3 py-2 text-xs shadow-lg ring-1 ring-border"
  style:left={`${left}px`} style:top={`${top}px`}>
  {@render children()}
</div>
