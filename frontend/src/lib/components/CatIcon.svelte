<script lang="ts">
  import { catLook } from "$lib/categories.svelte";
  import { cn } from "$lib/utils";

  // A category's emoji on a tint of its color (or, `solid`, on the color itself, like an iOS Settings icon).
  // `size` is the square's side in pixels.
  let { name, size = 24, solid = false, class: className }: {
    name: string | null | undefined; size?: number; solid?: boolean; class?: string;
  } = $props();
  const look = $derived(catLook(name));
</script>

<span aria-hidden="true" class={cn("inline-flex shrink-0 items-center justify-center leading-none", solid ? "rounded-lg" : "rounded-md", className)}
  style={`width:${size}px;height:${size}px;font-size:${Math.round(size * 0.56)}px;background:${solid ? look.color : `color-mix(in oklab, ${look.color} 24%, transparent)`}`}
>{look.icon}</span>
