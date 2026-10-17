<script lang="ts">
  import * as Popover from "$lib/components/ui/popover";
  import * as Card from "$lib/components/ui/card";
  import { fmt0, pct as share } from "$lib/format";
  import Check from "@lucide/svelte/icons/check";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import { foldAllocation } from "./allocation";
  import { inv } from "./state.svelte";
  import type { AllocKey, Investments } from "./types";

  let { allocation }: { allocation: Investments["allocation"] } = $props();

  const ALLOC: [AllocKey, string][] = [["asset_class", "Asset class"], ["account", "Account"], ["sector", "Sector"], ["holding", "Top holdings"]];
  // The chart colors; "Other" is muted.
  const COLORS = ["var(--nw-1)", "var(--nw-2)", "var(--nw-3)", "var(--nw-4)", "var(--nw-5)", "var(--nw-6)"];
  const color = (i: number, other?: boolean) => (other ? "var(--muted-foreground)" : COLORS[i % COLORS.length]);

  let open = $state(false);
  const rows = $derived(foldAllocation(allocation[inv.allocTab] ?? []));
  const label = $derived(ALLOC.find(([k]) => k === inv.allocTab)?.[1] ?? "");
  const pick = (k: AllocKey) => { inv.allocTab = k; open = false; };
</script>

<Card.Root data-testid="allocation">
  <Card.Header>
    <Card.Title>Allocation</Card.Title>
    <Card.Action>
      <Popover.Root bind:open>
        <Popover.Trigger class="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-[var(--edge)] bg-card px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 phone:min-h-11"
          aria-label={`Allocation by: ${label}`}>
          <span class="text-muted-foreground">By</span> {label}
          <ChevronDown class="size-4 opacity-70" aria-hidden="true" />
        </Popover.Trigger>
        <Popover.Content class="w-48 p-1" align="end">
          <div class="flex flex-col" role="group" aria-label="Allocation by">
            {#each ALLOC as [key, text] (key)}
              <button type="button" class="flex min-h-9 cursor-pointer items-center justify-between rounded-md px-2 text-left text-sm hover:bg-muted phone:min-h-11"
                aria-pressed={inv.allocTab === key} onclick={() => pick(key)}>
                {text}{#if inv.allocTab === key}<Check class="size-4 text-primary" aria-hidden="true" />{/if}
              </button>
            {/each}
          </div>
        </Popover.Content>
      </Popover.Root>
    </Card.Action>
  </Card.Header>
  <Card.Content>
    {#if rows.length}
      <div class="flex h-3 gap-[3px]" role="img" aria-label={`Allocation by ${label}`} data-testid="alloc-bar">
        {#each rows as r, i (r.name)}
          {#if r.share > 0}<span class="block h-full min-w-1 rounded-full" style:flex={`${r.share} 1 0`} style:background={color(i, r.other)}></span>{/if}
        {/each}
      </div>
      <ul class="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm" data-testid="alloc-legend">
        {#each rows as r, i (r.name)}
          <li class="flex min-w-0 items-center gap-2" data-testid="alloc-row">
            <span class="size-2.5 shrink-0 rounded-full" style:background={color(i, r.other)} aria-hidden="true"></span>
            <span class="min-w-0 flex-1 truncate">{r.name}</span>
            <span class="tabular-nums">{share(r.share)}</span>
            <span class="hidden w-20 text-right text-muted-foreground tabular-nums md:block">{fmt0(r.value)}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </Card.Content>
</Card.Root>
