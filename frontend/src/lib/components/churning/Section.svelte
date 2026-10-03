<script lang="ts">
  import Chip from "./Chip.svelte";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";

  // One collapsible part of the card form, in the style of Settings → Connections' rows: its title, and while it's
  // closed a line saying what's in it (so nothing is hidden without a hint). `flagged` marks one holding something the
  // server refused; the form opens it too.
  // `error` is what the server refused, said at the top of the section too (the form also says it by its Add button).
  let { id, title, summary, open = $bindable(false), flagged = false, error = "", children }: {
    id: string; title: string; summary: string; open?: boolean; flagged?: boolean; error?: string; children: Snippet;
  } = $props();
</script>

<details bind:open class="group mt-3 rounded-xl border bg-background" data-testid={`section-${id}`}>
  <summary class="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-xl px-3 py-2 select-none [&::-webkit-details-marker]:hidden">
    <span class="text-sm font-medium">{title}</span>
    <span class="min-w-0 flex-1 truncate text-xs text-muted-foreground" data-testid={`summary-${id}`}>{open ? "" : summary}</span>
    {#if flagged}<Chip tone="warn" data-testid={`flagged-${id}`}>Check this</Chip>{/if}
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>
  <div class="border-t px-3 py-3">
    {#if flagged && error}<p class="mb-3 text-sm text-destructive" data-testid={`error-${id}`}>{error}</p>{/if}
    {@render children()}
  </div>
</details>
