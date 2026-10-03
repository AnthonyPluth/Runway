<script lang="ts">
  import { cn, type WithoutChildrenOrChild } from "$lib/utils";
  import { Dialog } from "bits-ui";
  import XIcon from "@lucide/svelte/icons/x";
  import type { Snippet } from "svelte";

  // z-[60] keeps the sheet (and its backdrop) above MobileNav's tab bar and More menu (z-50).
  let { ref = $bindable(null), class: className, children, ...restProps }: WithoutChildrenOrChild<Dialog.ContentProps> & { children?: Snippet } = $props();
</script>

<Dialog.Portal>
  <Dialog.Overlay data-slot="sheet-overlay"
    class="fixed inset-0 z-[60] bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
  <Dialog.Content bind:ref data-slot="sheet-content"
    class={cn(
      "bg-card text-card-foreground fixed z-[60] flex flex-col gap-4 overflow-y-auto shadow-2xl outline-none",
      // Phone: bottom sheet.
      "inset-x-0 bottom-0 max-h-[90dvh] rounded-t-2xl border-t pb-[env(safe-area-inset-bottom)]",
      "data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom data-[state=closed]:animate-out data-[state=closed]:slide-out-to-bottom",
      // Desktop: right-hand panel.
      "md:inset-y-0 md:right-0 md:left-auto md:bottom-auto md:h-full md:max-h-none md:w-[440px] md:max-w-full md:rounded-none md:border-t-0 md:border-l md:pb-0",
      "md:data-[state=open]:slide-in-from-bottom-0 md:data-[state=open]:slide-in-from-right md:data-[state=closed]:slide-out-to-bottom-0 md:data-[state=closed]:slide-out-to-right",
      "duration-200",
      className,
    )}
    {...restProps}>
    {@render children?.()}
    <!-- The ✕ stays small; its padding makes a 40px target to tap. -->
    <Dialog.Close data-slot="sheet-x"
      class="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute top-2 right-2 cursor-pointer rounded-md p-3 focus-visible:ring-2 focus-visible:outline-none">
      <XIcon class="size-4" />
      <span class="sr-only">Close</span>
    </Dialog.Close>
  </Dialog.Content>
</Dialog.Portal>
