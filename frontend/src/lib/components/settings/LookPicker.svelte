<script lang="ts">
  import { api } from "$lib/api";
  import { CAT_EMOJI, lastEmoji, loadCategories } from "$lib/categories.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { searchEmoji } from "$lib/emoji";
  import { Button } from "$lib/components/ui/button";
  import type { Category } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";

  // A category's emoji: click the icon, then search by name ("coffee", "car"), type or paste any emoji (the phone's emoji
  // keyboard, or the computer's emoji panel), or pick one of the grid's. A web page can't open the device's own emoji
  // picker, so the box points to it instead. Saves as you pick; Reset goes back to the default (and clears any color set before,
  // which isn't offered here any more).
  let { c }: { c: Category } = $props();
  let open = $state(false);
  let root = $state<HTMLElement>();
  let trigger = $state<HTMLButtonElement>();
  // Escape and Done put the focus back on the icon; a click elsewhere leaves it where you clicked.
  function close(refocus = true) {
    open = false;
    if (refocus) trigger?.focus();
  }

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
  // Words search the emoji by name; an emoji typed or pasted is saved as it is.
  const found = $derived(searchEmoji(typed));
  const searching = $derived(typed.trim() !== "");
  function onType() {
    const e = lastEmoji(typed);
    if (!e) return;       // letters and the like: a search, nothing to save
    typed = "";
    pickIcon(e);
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter" && found.length) { e.preventDefault(); pickIcon(found[0]); typed = ""; }
  }
  // Where a computer keeps its emoji panel (phones have an emoji key on the keyboard instead).
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  const shortcut = /iPhone|iPad|Android/.test(ua) ? "" : /Mac/.test(ua) ? "Ctrl+⌘+Space" : /Windows/.test(ua) ? "Win+." : "";
  function outside(e: MouseEvent) { if (open && root && !root.contains(e.target as Node)) close(false); }
</script>

<svelte:window onclick={outside} onkeydown={(e) => { if (open && e.key === "Escape") { e.preventDefault(); close(); } }} />

<span class="relative inline-flex" bind:this={root}>
  <button type="button" bind:this={trigger} class="flex cursor-pointer items-center justify-center rounded-full phone:size-10 ring-offset-2 ring-offset-card hover:ring-2 hover:ring-ring/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    title="Change the emoji" aria-label={`Emoji for ${c.name}`} aria-haspopup="dialog" aria-expanded={open} onclick={() => (open ? close() : (open = true))}>
    <CatIcon name={c.name} size={28} />
  </button>
  {#if open}
    <div data-editor role="dialog" aria-label={`Emoji for ${c.name}`}
      class="absolute top-9 left-0 z-20 w-[20.5rem] max-w-[calc(100vw-2rem)] rounded-lg phone:top-11 border bg-popover p-3 text-popover-foreground shadow-lg">
      <div class="mb-1.5 text-xs font-medium text-muted-foreground">Emoji</div>
      <input bind:this={box} bind:value={typed} oninput={onType} onkeydown={onKey} type="search" autocomplete="off" spellcheck="false" enterkeyhint="done"
        class="mb-1 h-8 w-full rounded-md border bg-background px-2 text-base" placeholder="Search, or type any emoji"
        aria-label={`Search or type an emoji for ${c.name}`} />
      <p class="mb-2 text-xs text-muted-foreground">{shortcut ? `${shortcut} opens your computer’s emoji panel.` : "Your keyboard’s emoji key has every emoji."}</p>
      <div class="mt-2 mb-1 text-xs text-muted-foreground">{searching ? (found.length ? "Matches" : "No matches; try another word") : "Or pick one"}</div>
      <div class="grid max-h-72 grid-cols-8 gap-0.5 overflow-y-auto">
        {#each searching ? found : CAT_EMOJI as e (e)}
          <button type="button" class={cn("flex size-9 cursor-pointer items-center justify-center rounded-md text-xl hover:bg-muted", c.icon === e && "bg-muted ring-1 ring-ring")}
            aria-label={`Use ${e}`} onclick={() => pickIcon(e)}>{e}</button>
        {/each}
      </div>
      <div class="mt-3 flex justify-between">
        <Button variant="link" size="sm" class="px-0" disabled={!c.custom_icon && !c.custom_color} onclick={() => save(null, null)}>Reset to default</Button>
        <Button variant="outline" size="sm" onclick={() => close()}>Done</Button>
      </div>
    </div>
  {/if}
</span>
