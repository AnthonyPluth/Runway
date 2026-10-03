<script lang="ts">
  import { cn, type WithoutChildrenOrChild } from "$lib/utils";
  import { Popover } from "bits-ui";
  import type { Snippet } from "svelte";

  // A small panel under its trigger (a filter's options). Above MobileNav (z-50), as the sheet is; never wider than the
  // screen less its gutters.
  let { ref = $bindable(null), class: className, sideOffset = 6, align = "start", children, ...restProps }:
    WithoutChildrenOrChild<Popover.ContentProps> & { children?: Snippet } = $props();
</script>

<Popover.Portal>
  <Popover.Content bind:ref {sideOffset} {align} collisionPadding={16}
    class={cn("bg-popover text-popover-foreground z-[60] w-72 max-w-[calc(100vw-2rem)] rounded-xl border p-3 shadow-xl outline-none",
      "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0", className)}
    {...restProps}>
    {@render children?.()}
  </Popover.Content>
</Popover.Portal>
