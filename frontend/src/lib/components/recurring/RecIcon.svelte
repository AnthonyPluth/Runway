<script lang="ts">
  import { app } from "$lib/app.svelte";

  // The account's institution logo (or its first letter), or ↻ when Runway doesn't know the institution.
  let { id }: { id: string } = $props();
  const b = $derived(app.state?.brands?.[id]);
  const box = "flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-semibold text-muted-foreground";
</script>

{#if b?.logo}
  <img class="size-7 shrink-0 rounded-md" src={`/banks/${b.logo}.svg`} alt="" title={b.institution ?? ""} width="28" height="28" loading="lazy" />
{:else if b}
  <span class={box} title={b.institution ?? ""} aria-hidden="true">{b.initial}</span>
{:else}
  <span class={box} aria-hidden="true">↻</span>
{/if}
