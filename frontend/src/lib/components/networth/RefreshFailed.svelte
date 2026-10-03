<script lang="ts">
  import { cn } from "$lib/utils";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  // Over numbers already on screen when loading them again failed: they stay, and this says they may be out of date,
  // with Retry. `error` (the reason) is in the tooltip.
  let { error, onretry, class: cls }: { error: string; onretry: () => Promise<unknown> | void; class?: string } = $props();
  let busy = $state(false);
  async function retry() {
    busy = true;
    try { await onretry(); } finally { busy = false; }
  }
</script>

<div role="alert" class={cn("mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-[var(--warning)]/40 bg-card px-3 py-2 text-sm", cls)}
  data-testid="refresh-failed" title={error}>
  <TriangleAlert class="size-4 shrink-0 text-warning" aria-hidden="true" />
  <span><span class="font-medium">Couldn’t refresh</span> <span class="text-muted-foreground">· showing earlier numbers</span></span>
  <span class="text-muted-foreground" aria-hidden="true">·</span>
  <button type="button" class="cursor-pointer font-medium underline underline-offset-4 disabled:cursor-default disabled:opacity-60 max-md:min-h-10" disabled={busy} onclick={retry}>
    {busy ? "Retrying…" : "Retry"}
  </button>
</div>
