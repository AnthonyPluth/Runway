<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAttributes } from "svelte/elements";

  let { ref = $bindable(null), class: className, children, ...restProps }: WithElementRef<HTMLAttributes<HTMLElement>> = $props();
</script>

<div bind:this={ref} data-slot="card" class={cn("rounded-xl bg-card text-card-foreground ring-1 ring-border p-5 sm:p-6", className)} {...restProps}>
  {@render children?.()}
</div>
