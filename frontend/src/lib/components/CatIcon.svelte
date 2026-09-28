<script lang="ts">
  import { catLook } from "$lib/categories.svelte";
  import { cn } from "$lib/utils";

  // A category's emoji on a tint of its color. `size` is the square's side in pixels.
  let { name, size = 24, class: className }: { name: string | null | undefined; size?: number; class?: string } = $props();
  const look = $derived(catLook(name));
</script>

<span aria-hidden="true" class={cn("inline-flex shrink-0 items-center justify-center rounded-md leading-none", className)}
  style={`width:${size}px;height:${size}px;font-size:${Math.round(size * 0.56)}px;background:color-mix(in oklab, ${look.color} 24%, transparent)`}
>{look.icon}</span>
