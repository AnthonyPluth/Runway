<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";

  // One collapsible part of the card form, in the style of Settings → Connections' rows: its title, and while it's
  // closed a line saying what's in it (so nothing is hidden without a hint). `flagged` marks one holding something the
  // server refused; the form opens it too.
  let { id, title, summary, open = $bindable(false), flagged = false, children }: {
    id: string; title: string; summary: string; open?: boolean; flagged?: boolean; children: Snippet;
  } = $props();
</script>

<details bind:open class="group mt-3 rounded-xl border bg-background" data-testid={`section-${id}`}>
  <summary class="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-xl px-3 py-2 select-none [&::-webkit-details-marker]:hidden">
    <span class="text-sm font-medium">{title}</span>
    <span class="min-w-0 flex-1 truncate text-xs text-muted-foreground" data-testid={`summary-${id}`}>{open ? "" : summary}</span>
    {#if flagged}<Badge variant="outline" class="border-amber-500/50 text-amber-500" data-testid={`flagged-${id}`}>Check this</Badge>{/if}
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>
  <div class="border-t px-3 py-3">{@render children()}</div>
</details>
