<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLAttributes } from "svelte/elements";

  let { ref = $bindable(null), class: className, children, ...restProps }: WithElementRef<HTMLAttributes<HTMLElement>> = $props();
</script>

<div bind:this={ref} data-slot="sheet-footer" class={cn("mt-auto flex flex-col-reverse gap-2 p-4 sm:flex-row sm:justify-end", className)} {...restProps}>
  {@render children?.()}
</div>
