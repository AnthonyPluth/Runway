<script lang="ts">
  import { Button, type ButtonSize, type ButtonVariant } from "$lib/components/ui/button";
  import { onDestroy, type Snippet } from "svelte";

  // A button for something hard to undo: the first click asks ("Remove?") for 4 seconds, the second one does it.
  // Both labels are always laid out in the same spot (the one not showing invisible), so the button is as wide as the
  // longer one and nothing beside it moves when it asks; a screen reader hears the question.
  let { confirm, onconfirm, variant = "link", size = "sm", class: className, disabled = false, title, children }: {
    confirm: string; onconfirm: () => void | Promise<void>; variant?: ButtonVariant; size?: ButtonSize; class?: string;
    disabled?: boolean; title?: string; children: Snippet;
  } = $props();
  let armed = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  function click() {
    if (!armed) { armed = true; clearTimeout(timer); timer = setTimeout(() => (armed = false), 4000); return; }
    armed = false; clearTimeout(timer);
    onconfirm();
  }
  onDestroy(() => clearTimeout(timer));
</script>

<Button {variant} {size} {disabled} {title} class={className} onclick={click}>
  <span class="grid justify-items-center [&>*]:col-start-1 [&>*]:row-start-1">
    <span class={armed ? "invisible" : undefined} aria-hidden={armed}>{@render children()}</span>
    <span class={armed ? undefined : "invisible"} aria-hidden={!armed}>{confirm}</span>
  </span>
</Button>
<span class="sr-only" aria-live="polite">{armed ? confirm : ""}</span>
