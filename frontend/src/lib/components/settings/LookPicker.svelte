<script lang="ts">
  import { api } from "$lib/api";
  import { CAT_EMOJI, lastEmoji, loadCategories } from "$lib/categories.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { Button } from "$lib/components/ui/button";
  import type { Category } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";

  // A category's emoji: click the icon, then type or paste any emoji (the phone's emoji keyboard, or the computer's emoji
  // panel), or pick one of the grid's. Saves as you pick; Reset goes back to the default (and clears any color set before,
  // which isn't offered here any more).
  let { c }: { c: Category } = $props();
  let open = $state(false);
  let root = $state<HTMLElement>();

  async function save(icon: string | null | undefined, color: string | null | undefined) {
    try {
      await api("/api/categories/look", { method: "POST", body: { name: c.name, icon: icon ?? "", color: color ?? "" } });
      await loadCategories();
    } catch (err) { toast.error((err as Error).message); }
  }
  const pickIcon = (icon: string) => save(icon, c.custom_color);
  let typed = $state("");
  let box = $state<HTMLInputElement>();
  $effect(() => { if (open) box?.focus(); });
  function onType() {
    const e = lastEmoji(typed);
    if (!e) return;       // letters and the like: nothing to save yet
    typed = "";
    pickIcon(e);
  }
  // Where a computer keeps its emoji panel (phones have an emoji key on the keyboard instead).
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  const shortcut = /iPhone|iPad|Android/.test(ua) ? "" : /Mac/.test(ua) ? "Ctrl+⌘+Space" : /Windows/.test(ua) ? "Win+." : "";
  function outside(e: MouseEvent) { if (open && root && !root.contains(e.target as Node)) open = false; }
</script>

<svelte:window onclick={outside} onkeydown={(e) => { if (open && e.key === "Escape") open = false; }} />

<span class="relative" bind:this={root}>
  <button type="button" class="cursor-pointer rounded-full ring-offset-2 ring-offset-card hover:ring-2 hover:ring-ring/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    title="Change the emoji" aria-label={`Emoji for ${c.name}`} aria-expanded={open} onclick={() => (open = !open)}>
    <CatIcon name={c.name} size={28} />
  </button>
  {#if open}
    <div data-editor role="dialog" aria-label={`Emoji for ${c.name}`}
      class="absolute top-9 left-0 z-20 w-72 rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg">
      <div class="mb-1.5 text-xs font-medium text-muted-foreground">Emoji</div>
      <input bind:this={box} bind:value={typed} oninput={onType} type="text" autocomplete="off" spellcheck="false" enterkeyhint="done"
        class="mb-1 h-8 w-full rounded-md border bg-background px-2 text-base" placeholder="Type or paste any emoji"
        aria-label={`Type an emoji for ${c.name}`} />
      {#if shortcut}<p class="mb-2 text-xs text-muted-foreground">{shortcut} opens your computer’s emoji panel.</p>{/if}
      <div class="mt-2 mb-1 text-xs text-muted-foreground">Or pick one</div>
      <div class="grid grid-cols-10 gap-0.5">
        {#each CAT_EMOJI as e (e)}
          <button type="button" class={cn("flex size-6 cursor-pointer items-center justify-center rounded text-base hover:bg-muted", c.icon === e && "bg-muted ring-1 ring-ring")}
            aria-label={`Use ${e}`} onclick={() => pickIcon(e)}>{e}</button>
        {/each}
      </div>
      <div class="mt-3 flex justify-between">
        <Button variant="link" size="sm" class="px-0" disabled={!c.custom_icon && !c.custom_color} onclick={() => save(null, null)}>Reset to default</Button>
        <Button variant="outline" size="sm" onclick={() => (open = false)}>Done</Button>
      </div>
    </div>
  {/if}
</span>
