<script lang="ts">
  import { app } from "$lib/app.svelte";

  // An account's name with its institution's logo (from runway/static/banks), or its first letter.
  let { id, name }: { id: string; name?: string } = $props();
  const b = $derived(app.state?.brands?.[id]);
</script>

<span class="inline-flex min-w-0 items-center gap-2">
  {#if b?.logo}
    <img class="size-[18px] shrink-0 rounded" src={`/banks/${b.logo}.svg`} alt="" title={b.institution ?? ""} width="18" height="18" loading="lazy" />
  {:else if b}
    <span class="flex size-[18px] shrink-0 items-center justify-center rounded bg-muted text-[10px] font-semibold text-muted-foreground"
      title={b.institution ?? ""} aria-hidden="true">{b.initial}</span>
  {/if}
  <span class="truncate">{name ?? ""}</span>
</span>
