<script lang="ts">
  import { api } from "$lib/api";
  import { CAT_EMOJI, CAT_PALETTE, loadCategories } from "$lib/categories.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import { Button } from "$lib/components/ui/button";
  import type { Category } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";

  // A category's emoji and color: click the icon to pick them. Saves as you pick; Reset goes back to the default.
  let { c }: { c: Category } = $props();
  let open = $state(false);
  let root = $state<HTMLElement>();
  let typed = $state("");

  async function save(icon: string | null | undefined, color: string | null | undefined) {
    try {
      await api("/api/categories/look", { method: "POST", body: { name: c.name, icon: icon ?? "", color: color ?? "" } });
      await loadCategories();
    } catch (err) { toast.error((err as Error).message); }
  }
  const pickIcon = (icon: string) => save(icon, c.custom_color);
  const pickColor = (color: string) => save(c.custom_icon, color);
  function onTyped() {
    // Keep only the last emoji typed (an emoji keyboard may add more than one).
    const seg = [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(typed.trim())].map((s) => s.segment);
    const icon = seg.at(-1);
    if (icon) pickIcon(icon);
    typed = "";
  }
  function outside(e: MouseEvent) { if (open && root && !root.contains(e.target as Node)) open = false; }
</script>

<svelte:window onclick={outside} onkeydown={(e) => { if (open && e.key === "Escape") open = false; }} />

<span class="relative" bind:this={root}>
  <button type="button" class="cursor-pointer rounded-full ring-offset-2 ring-offset-card hover:ring-2 hover:ring-ring/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    title="Change the emoji and color" aria-label={`Emoji and color for ${c.name}`} aria-expanded={open} onclick={() => (open = !open)}>
    <CatIcon name={c.name} size={28} class="rounded-full" />
  </button>
  {#if open}
    <div data-editor role="dialog" aria-label={`Emoji and color for ${c.name}`}
      class="absolute top-9 left-0 z-20 w-72 rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg">
      <div class="mb-1.5 text-xs font-medium text-muted-foreground">Emoji</div>
      <div class="grid grid-cols-10 gap-0.5">
        {#each CAT_EMOJI as e (e)}
          <button type="button" class={cn("flex size-6 cursor-pointer items-center justify-center rounded text-base hover:bg-muted", c.icon === e && "bg-muted ring-1 ring-ring")}
            aria-label={`Use ${e}`} onclick={() => pickIcon(e)}>{e}</button>
        {/each}
      </div>
      <input class="mt-2 h-8 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring dark:bg-input/30"
        placeholder="Or type any emoji" aria-label="Type an emoji" bind:value={typed} onchange={onTyped} onkeydown={(e) => e.key === "Enter" && onTyped()} />
      <div class="mt-3 mb-1.5 text-xs font-medium text-muted-foreground">Color</div>
      <div class="grid grid-cols-10 gap-1">
        {#each CAT_PALETTE as col (col)}
          <button type="button" class={cn("size-5 cursor-pointer rounded-full ring-offset-2 ring-offset-popover", c.color === col && "ring-2 ring-foreground")}
            style:background={col} aria-label={`Use color ${col}`} aria-pressed={c.color === col} onclick={() => pickColor(col)}></button>
        {/each}
      </div>
      <div class="mt-3 flex justify-between">
        <Button variant="link" size="sm" class="px-0" disabled={!c.custom_icon && !c.custom_color} onclick={() => save(null, null)}>Reset to default</Button>
        <Button variant="outline" size="sm" onclick={() => (open = false)}>Done</Button>
      </div>
    </div>
  {/if}
</span>
