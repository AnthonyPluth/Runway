<script lang="ts">
  import { app, syncNow } from "$lib/app.svelte";
  import { cn } from "$lib/utils";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";

  // The Sync button beside the sync status (sidebar, and the More sheet on a phone). Only with a bank connected.
  let { class: className = "" }: { class?: string } = $props();
  const busy = $derived(!!app.state?.syncing);
</script>

{#if app.state?.connected}
  <button type="button" onclick={syncNow} disabled={busy} title="Sync now" aria-label="Sync now"
    class={cn("flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground disabled:cursor-default disabled:opacity-60 disabled:hover:bg-transparent", className)}>
    <RefreshCw class={cn("size-3.5", busy && "animate-spin")} aria-hidden="true" />
  </button>
{/if}
