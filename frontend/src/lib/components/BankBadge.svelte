<script lang="ts">
  import { app } from "$lib/app.svelte";
  import { badgeShape } from "$lib/logoTone";
  import { cn } from "$lib/utils";

  // An account's institution as a small badge over the corner of another logo (a transaction's merchant): its logo, or its
  // first letter. A logo that's a solid tile (Chase's) is drawn as it is; a sparse one, like a wordmark with nothing
  // behind it (Citi's), gets a white tile behind it so it reads at this size (a dark one for a near-white mark).
  // `class` places it (overriding the corner it sits on by default); `size` sizes it; `name` is its tooltip.
  let { accountId, name = "", size = "size-4 rounded-md text-xs lg:size-3 lg:rounded-[3px] lg:text-[8px]", class: className }:
    { accountId: string; name?: string; size?: string; class?: string } = $props();
  const b = $derived(app.state?.brands?.[accountId]);
  let tile = $state<"" | "light" | "dark">("");

  function sample(img: HTMLImageElement) {
    const decide = () => {
      if (!img.naturalWidth) return;
      const s = badgeShape(img);
      tile = s.coverage >= 0.6 && s.square ? "" : s.light ? "dark" : "light";
    };
    if (img.complete) decide();
    img.addEventListener("load", decide);
    return () => img.removeEventListener("load", decide);
  }
</script>

{#if b}
  <span class={cn("pointer-events-none absolute -right-1.5 -bottom-1.5 z-[1] lg:-right-1 lg:-bottom-1", className)} title={name || b.institution || undefined}
    data-account-badge data-tile={tile || undefined}>
    {#if b.src}
      <span class={cn("flex shrink-0 items-center justify-center overflow-hidden", size, tile === "light" && "bg-white p-[2px]", tile === "dark" && "bg-card p-[2px]")}>
        <img class={cn("size-full", tile ? "object-contain" : "rounded-[inherit]")} src={b.src} alt="" width="16" height="16" loading="lazy" {@attach sample} />
      </span>
    {:else}
      <span class={cn("flex shrink-0 items-center justify-center bg-muted font-semibold text-muted-foreground", size)} aria-hidden="true">{b.initial}</span>
    {/if}
  </span>
{/if}
