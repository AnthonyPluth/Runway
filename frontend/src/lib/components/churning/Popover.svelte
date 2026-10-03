<script lang="ts">
  import { Button, type ButtonSize, type ButtonVariant } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  // A small panel that opens under its button: a "…" menu of secondary actions, or the amount to mark a credit used.
  // Click outside or Escape closes it (Escape puts focus back on the button); `children` gets `close` for its actions.
  let { label, trigger, children, variant = "ghost", size = "sm", align = "end", class: cls, panelClass }: {
    label: string; trigger: Snippet; children: Snippet<[() => void]>; variant?: ButtonVariant; size?: ButtonSize;
    align?: "start" | "end"; class?: string; panelClass?: string;
  } = $props();
  let open = $state(false);
  let box = $state<HTMLElement | null>(null), button = $state<HTMLElement | null>(null);
  const close = () => { open = false; };
  function away(e: Event) { if (open && box && !box.contains(e.target as Node)) open = false; }
  function key(e: KeyboardEvent) { if (e.key === "Escape" && open) { e.stopPropagation(); open = false; button?.focus(); } }
</script>

<svelte:document onpointerdown={away} />

<div bind:this={box} class="relative inline-block" onkeydown={key} role="presentation">
  <Button bind:ref={button} {variant} {size} class={cn("px-2", cls)} aria-label={label} aria-haspopup="dialog" aria-expanded={open} onclick={() => (open = !open)}>{@render trigger()}</Button>
  {#if open}
    <div role="dialog" aria-label={label}
      class={cn("absolute top-full z-30 mt-1 flex min-w-40 flex-col gap-1 rounded-lg border bg-popover p-2 text-sm shadow-lg", align === "end" ? "right-0" : "left-0", panelClass)}>
      {@render children(close)}
    </div>
  {/if}
</div>
