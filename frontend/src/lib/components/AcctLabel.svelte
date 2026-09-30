<script lang="ts">
  import { app } from "$lib/app.svelte";

  // An account's name with its institution's logo (bundled, or Logo.dev's), or its first letter.
  let { id, name }: { id: string; name?: string } = $props();
  const b = $derived(app.state?.brands?.[id]);
</script>

<span class="inline-flex min-w-0 items-center gap-2">
  {#if b?.src}
    <img class="size-5 shrink-0 rounded" src={b.src} alt="" title={b.institution ?? ""} width="18" height="18" loading="lazy" />
  {:else if b}
    <span class="flex size-5 shrink-0 items-center justify-center rounded bg-muted text-xs font-semibold text-muted-foreground"
      title={b.institution ?? ""} aria-hidden="true">{b.initial}</span>
  {/if}
  <span class="truncate">{name ?? ""}</span>
</span>
