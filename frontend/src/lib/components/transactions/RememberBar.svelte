<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { always, closeRemember, remember } from "./remember.svelte";

  // "Saved. Always use Groceries for Whole Foods?" at the bottom of the screen; Escape (or 12 seconds) closes it.
  const a = $derived(remember.ask);
</script>

<svelte:window onkeydown={(e) => { if (e.key === "Escape" && remember.ask) closeRemember(); }} />

{#if a}
  <div role="dialog" aria-label="Use this category for the merchant from now on?"
    class="fixed bottom-[calc(1.5rem+env(safe-area-inset-bottom))] left-1/2 z-50 flex w-[min(40rem,calc(100vw-2rem))] -translate-x-1/2 flex-wrap items-center gap-3 rounded-lg border bg-popover p-3 pl-4 text-sm text-popover-foreground shadow-lg">
    <span>Saved. Always use <b>{a.category}</b> for <b>{a.offer.merchant}</b>?{#if a.offer.replaces}
      <span class="text-xs text-muted-foreground">(instead of {a.offer.replaces})</span>{/if}</span>
    <span class="ml-auto flex gap-2">
      <Button size="sm" onclick={always}>Always</Button>
      <Button size="sm" variant="outline" onclick={closeRemember}>Just this once</Button>
    </span>
  </div>
{/if}
