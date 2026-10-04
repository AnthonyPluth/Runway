<script lang="ts">
  import type { StatementEstimate } from "$lib/types";
  import { estimateHeading, estimateLines } from "./estimate";

  // An estimated statement's parts, a line each with its amount on the right and what it's from under it, in a row's
  // muted second-line style: shown under the row when its asterisk is tapped, for a touch
  // screen, where a tooltip never shows.
  let { estimate, id, class: className }: { estimate: StatementEstimate; id?: string; class?: string } = $props();
  const lines = $derived(estimateLines(estimate));
</script>

<div {id} class={["text-[13px] text-muted-foreground tabular-nums", className]}>
  <div class="font-medium">{estimateHeading(estimate)}</div>
  {#each lines as l, i (i)}
    <div class="flex items-baseline justify-between gap-3">
      <span class="min-w-0">{l.label}</span>
      <span class={["shrink-0 whitespace-nowrap", l.sum && !l.label && "font-medium text-foreground"]}>{l.value}</span>
    </div>
    {#if l.detail}<div class="pr-20 text-muted-foreground/70">{l.detail}</div>{/if}
  {/each}
</div>
