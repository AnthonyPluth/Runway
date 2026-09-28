<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAttributes } from "svelte/elements";

  let { ref = $bindable(null), class: className, children, ...restProps }: WithElementRef<HTMLAttributes<HTMLElement>> = $props();
</script>

<h2 bind:this={ref} data-slot="card-title" class={cn("text-base font-semibold text-foreground-strong", className)} {...restProps}>
  {@render children?.()}
</h2>
