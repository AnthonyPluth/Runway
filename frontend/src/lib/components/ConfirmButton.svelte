<script lang="ts">
  import { Button, type ButtonSize, type ButtonVariant } from "$lib/components/ui/button";
  import type { Snippet } from "svelte";

  // A button for something hard to undo: the first click asks ("Remove?") for 4 seconds, the second one does it.
  let { confirm, onconfirm, variant = "link", size = "sm", class: className, disabled = false, title, children }: {
    confirm: string; onconfirm: () => void | Promise<void>; variant?: ButtonVariant; size?: ButtonSize; class?: string;
    disabled?: boolean; title?: string; children: Snippet;
  } = $props();
  let armed = $state(false);
  let timer: ReturnType<typeof setTimeout>;
  function click() {
    if (!armed) { armed = true; clearTimeout(timer); timer = setTimeout(() => (armed = false), 4000); return; }
    armed = false; clearTimeout(timer);
    onconfirm();
  }
</script>

<Button {variant} {size} {disabled} {title} class={className} onclick={click}>
  {#if armed}{confirm}{:else}{@render children()}{/if}
</Button>
