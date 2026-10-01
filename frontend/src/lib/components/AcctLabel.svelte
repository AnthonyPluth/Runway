<script lang="ts">
  import { app } from "$lib/app.svelte";

  // An account's name with its institution's logo (bundled, or Logo.dev's), or its first letter (`iconClass` styles that mark, e.g. to hide it).
  // `labelClass` styles the name (e.g. to hide it, leaving only the logo).
  let { id, name, iconClass = "", labelClass = "" }: { id: string; name?: string; iconClass?: string; labelClass?: string } = $props();
  const b = $derived(app.state?.brands?.[id]);
</script>

<span class="inline-flex min-w-0 max-w-full items-center gap-2">
  {#if b?.src}
    <img class={`size-5 shrink-0 rounded ${iconClass}`} src={b.src} alt="" title={b.institution ?? ""} width="18" height="18" loading="lazy" />
  {:else if b}
    <span class={`flex size-5 shrink-0 items-center justify-center rounded bg-muted text-xs font-semibold text-muted-foreground ${iconClass}`}
      title={b.institution ?? ""} aria-hidden="true">{b.initial}</span>
  {/if}
  <span class={`truncate ${labelClass}`}>{name ?? ""}</span>
</span>
