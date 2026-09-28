<script lang="ts">
  import { cn, type WithElementRef } from "$lib/utils";
  import type { HTMLSelectAttributes } from "svelte/elements";

  // A styled native <select>: keyboard and screen-reader behavior for free, and the phone's own picker on mobile.
  // `ghost` hides the border until you hover or focus it, for selects inside table rows.
  let { ref = $bindable(null), value = $bindable(), ghost = false, class: className, children, ...restProps }:
    WithElementRef<HTMLSelectAttributes, HTMLSelectElement> & { ghost?: boolean } = $props();
</script>

<select bind:this={ref} bind:value data-slot="native-select"
  class={cn("border-input dark:bg-input/30 h-9 min-w-0 cursor-pointer rounded-md border bg-transparent py-1 pl-2.5 pr-8 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 [&>optgroup]:bg-popover [&_option]:bg-popover",
    ghost && "border-transparent shadow-none dark:bg-transparent hover:border-input focus-visible:border-ring", className)}
  {...restProps}>
  {@render children?.()}
</select>
