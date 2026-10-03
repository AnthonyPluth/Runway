<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";

  // One outside service under Settings → Connections (SimpleFIN, Plaid, the browser extension, OpenRouter, ...) as a
  // collapsed row: its name (with a `tag` such as "Recommended"), what it's for and where it stands ("On" or "Not set",
  // or what `status` says; "Needs attention" in the warning tone when it `warn`s, which also opens it). It opens to its
  // setup; `open` opens it to start with. Rows that share a `group` open one at a time, like the two ways to add a bank.
  let { name, purpose, on, status, warn = false, open = false, tag, group, children }: {
    name: string; purpose: string; on: boolean; status?: string; warn?: boolean; open?: boolean; tag?: string; group?: string;
    children: Snippet;
  } = $props();

  function toggled(e: Event) {
    const d = e.currentTarget as HTMLDetailsElement;
    if (!group || !d.open) return;
    for (const other of document.querySelectorAll<HTMLDetailsElement>("details[data-group]"))
      if (other !== d && other.dataset.group === group) other.open = false;
  }
</script>

<details class="group rounded-xl border bg-card text-card-foreground shadow-sm" open={open || warn} data-group={group} ontoggle={toggled}>
  <summary class="flex min-h-14 cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-2 select-none [&::-webkit-details-marker]:hidden">
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="flex flex-wrap items-center gap-x-2 gap-y-0.5"><span class="font-medium" data-service>{name}</span>{#if tag}<Badge
        variant="outline" class="border-good/40 text-good">{tag}</Badge>{/if}</span>
      <span class="truncate text-xs text-muted-foreground">{purpose}</span>
    </span>
    {#if warn}<Badge variant="outline" class="border-warning/50 text-warning" data-status="warn">{status ?? "Needs attention"}</Badge>
    {:else}<Badge variant={on ? "default" : "secondary"} data-status={on ? "on" : "off"}>{status ?? (on ? "On" : "Not set")}</Badge>{/if}
    <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
  </summary>
  <div class="flex flex-col gap-3 border-t px-4 py-4">{@render children()}</div>
</details>
