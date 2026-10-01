<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";

  // One outside service under Settings → Connections (SimpleFIN, Plaid, the browser extension, OpenRouter, ...) as a
  // collapsed row: its name, what it's for and where it stands ("On" or "Not set", or what `status` says; amber when it
  // `warn`s). It opens to its setup; `open` opens it to start with (nothing connected yet, or something that needs you).
  let { name, purpose, on, status, warn = false, open = false, children }: {
    name: string; purpose: string; on: boolean; status?: string; warn?: boolean; open?: boolean; children: Snippet;
  } = $props();
</script>

<details class="group rounded-xl border bg-card text-card-foreground shadow-sm" {open}>
  <summary class="flex min-h-14 cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-2 select-none [&::-webkit-details-marker]:hidden">
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="font-medium">{name}</span>
      <span class="truncate text-xs text-muted-foreground">{purpose}</span>
    </span>
    {#if warn}<Badge variant="outline" class="border-amber-500/50 text-amber-500" data-status="warn">{status ?? "Needs attention"}</Badge>
    {:else}<Badge variant={on ? "default" : "secondary"} data-status={on ? "on" : "off"}>{status ?? (on ? "On" : "Not set")}</Badge>{/if}
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>
  <div class="flex flex-col gap-3 border-t px-4 py-4">{@render children()}</div>
</details>
