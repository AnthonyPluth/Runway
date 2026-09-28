<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAttributes } from "svelte/elements";

  let { ref = $bindable(null), class: className, children, ...restProps }: WithElementRef<HTMLAttributes<HTMLElement>> = $props();
</script>

<div bind:this={ref} data-slot="card-header" class={cn("mb-4 flex flex-wrap items-center justify-between gap-3", className)} {...restProps}>
  {@render children?.()}
</div>
