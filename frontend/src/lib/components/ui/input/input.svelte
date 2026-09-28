<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLInputAttributes } from "svelte/elements";

  let { ref = $bindable(null), value = $bindable(), class: className, ...restProps }: WithElementRef<HTMLInputAttributes> = $props();
</script>

<input bind:this={ref} bind:value data-slot="input"
  class={cn("h-9 min-w-0 rounded-md border border-input bg-background px-2.5 py-1 text-sm tabular outline-none transition-[color,box-shadow] placeholder:text-faint-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/25 disabled:opacity-50", className)}
  {...restProps} />
