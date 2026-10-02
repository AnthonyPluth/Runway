<script lang="ts">
  import { catLook } from "$lib/categories.svelte";
  import { cn } from "$lib/utils";

  // A category's emoji on its own, with no tinted square behind it: the tints came from each category's color and
  // didn't match from one category to the next. `size` is the side of the square it takes up, in pixels.
  let { name, size = 24, class: className }: { name: string | null | undefined; size?: number; class?: string } = $props();
  const look = $derived(catLook(name));
</script>

<span aria-hidden="true" class={cn("inline-flex shrink-0 items-center justify-center leading-none", className)}
  style={`width:${size}px;height:${size}px;font-size:${Math.round(size * 0.75)}px`}
>{look.icon}</span>
