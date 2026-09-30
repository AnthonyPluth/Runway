<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";

  // One optional service (OpenRouter, Realie, Finnhub, Logo.dev) as a collapsed row: its name, what it's for and
  // whether a key is set. It opens to the key, the options and the privacy note.
  let { name, purpose, on, children }: { name: string; purpose: string; on: boolean; children: Snippet } = $props();
</script>

<details class="group rounded-xl border bg-card text-card-foreground shadow-sm">
  <summary class="flex min-h-14 cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-2 select-none [&::-webkit-details-marker]:hidden">
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="font-medium">{name}</span>
      <span class="truncate text-xs text-muted-foreground">{purpose}</span>
    </span>
    <Badge variant={on ? "default" : "secondary"} data-status={on ? "on" : "off"}>{on ? "On" : "Not set"}</Badge>
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>
  <div class="flex flex-col gap-3 border-t px-4 py-4">{@render children()}</div>
</details>
