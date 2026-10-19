<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import type { Investments } from "./types";

  // The checks that aren't fine: a card each (amber title for "worth a look", muted for a note). When all are fine,
  // one line says so instead. What the checks compute is the server's (`xray`); this only picks what to show.
  // `inline` is for the Accounts card's last row: the same content without a card of its own or the gap under it.
  let { checks, inline = false }: { checks: Investments["xray"]; inline?: boolean } = $props();
  const open = $derived(checks.filter((c) => !c.ok));
</script>

{#snippet cards()}
  <ul class="-mx-6 flex snap-x snap-mandatory gap-3 overflow-x-auto px-6 pb-1 md:mx-0 md:grid md:grid-cols-2 md:overflow-visible md:px-0 lg:grid-cols-3">
    {#each open as c (c.name)}
      <li class="w-64 shrink-0 snap-start rounded-xl border border-[var(--edge)] p-3 md:w-auto" data-testid="check-card">
        <p class={cn("text-sm font-medium", c.info ? "text-muted-foreground" : "text-warning")}>{c.name}</p>
        <p class="mt-1 text-sm text-muted-foreground">{c.detail}</p>
      </li>
    {/each}
  </ul>
{/snippet}

{#if open.length}
  {#if inline}
    <div class="mt-3 border-t border-border pt-3" data-testid="worth-a-look">
      <p class="mb-2 text-sm font-medium">Worth a look</p>
      {@render cards()}
    </div>
  {:else}
    <Card.Root class="mb-6" data-testid="worth-a-look">
      <Card.Header><Card.Title>Worth a look</Card.Title></Card.Header>
      <Card.Content>{@render cards()}</Card.Content>
    </Card.Root>
  {/if}
{:else if checks.length}
  <p class={inline ? "mt-3 border-t border-border pt-3 text-sm text-muted-foreground" : "mb-6 text-sm text-muted-foreground"} data-testid="checks-fine">All checks look fine</p>
{/if}
