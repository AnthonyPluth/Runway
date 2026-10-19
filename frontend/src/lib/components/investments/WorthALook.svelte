<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import type { Investments } from "./types";

  // The checks that aren't fine: a card each (amber title for "worth a look", muted for a note). When all are fine,
  // the Accounts card says so in one line instead (AccountsCard). What the checks compute is the server's (`xray`); this only picks what to show.
  let { checks }: { checks: Investments["xray"] } = $props();
  const open = $derived(checks.filter((c) => !c.ok));
</script>

{#if open.length}
  <Card.Root class="mb-6" data-testid="worth-a-look">
    <Card.Header><Card.Title>Worth a look</Card.Title></Card.Header>
    <Card.Content>
      <ul class="-mx-6 flex snap-x snap-mandatory gap-3 overflow-x-auto px-6 pb-1 md:mx-0 md:grid md:grid-cols-2 md:overflow-visible md:px-0 lg:grid-cols-3">
        {#each open as c (c.name)}
          <li class="w-64 shrink-0 snap-start rounded-xl border border-[var(--edge)] p-3 md:w-auto" data-testid="check-card">
            <p class={cn("text-sm font-medium", c.info ? "text-muted-foreground" : "text-warning")}>{c.name}</p>
            <p class="mt-1 text-sm text-muted-foreground">{c.detail}</p>
          </li>
        {/each}
      </ul>
    </Card.Content>
  </Card.Root>
{/if}
