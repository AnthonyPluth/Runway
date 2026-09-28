<script lang="ts">
  import { api } from "$lib/api";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmtDate } from "$lib/format";
  import { cn } from "$lib/utils";
  import { SvelteSet } from "svelte/reactivity";
  import { Report } from "./chart.svelte";
  import MerchantDetail from "./MerchantDetail.svelte";
  import { rangeDates, rangeOptions, reportState as st, type RangeKey } from "./state.svelte";
  import Status from "./Status.svelte";
  import type { MerchantsReport } from "./types";

  // Merchants: where the money went, biggest first. Open one for its months and transactions.
  const report = new Report(() => {
    const { start, end } = rangeDates(st.range);
    return api<MerchantsReport>(`/api/reports/merchants?start=${start}&end=${end}`);
  });
  let q = $state("");
  const open = new SvelteSet<string>();
  const top = $derived(report.data?.merchants[0]?.total || 1);
  const needle = $derived(q.trim().toLowerCase());
  const toggle = (name: string) => { if (open.has(name)) open.delete(name); else open.add(name); };
  // A merchant the search hides closes too.
  function closeHidden(e: Event) {
    const s = (e.currentTarget as HTMLInputElement).value.trim().toLowerCase();
    for (const name of open) if (s && !name.toLowerCase().includes(s)) open.delete(name);
  }
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Period" value={st.range} options={rangeOptions} onchange={(v) => { st.range = v as RangeKey; open.clear(); q = ""; report.load(); }} />
  <Input type="search" bind:value={q} oninput={closeHidden} placeholder="Find a merchant" aria-label="Find a merchant" class="max-w-[260px]" />
</div>

{#if !report.data || report.error}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  <Card.Root>
    <Card.Content>
      {#if d.merchants.length}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4">
                <th>Merchant</th><th class="max-sm:hidden">Usual category</th><th class="text-right">Visits</th>
                <th class="text-right max-sm:hidden">Average</th><th class="max-sm:hidden">Last</th><th class="text-right">Spent</th>
              </tr>
            </thead>
            <tbody>
              {#each d.merchants as x (x.name)}
                {#if !needle || x.name.toLowerCase().includes(needle)}
                  {@const isOpen = open.has(x.name)}
                  <tr class={cn("relative border-t hover:bg-muted/50 [&>td]:py-2.5 [&>td+td]:pl-4", isOpen && "bg-muted/50")}>
                    <td class="whitespace-nowrap">
                      <button class="cursor-pointer text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                        aria-expanded={isOpen} onclick={() => toggle(x.name)}>{x.name}</button>
                      <span class="mt-1 block h-1 rounded-sm bg-(--flow-out) opacity-55" style:width={`${Math.max(2, (x.total / top) * 100)}%`}></span>
                    </td>
                    <td class="text-muted-foreground max-sm:hidden">{x.category}</td>
                    <td class="text-right tabular-nums">{x.count}</td>
                    <td class="text-right text-muted-foreground tabular-nums max-sm:hidden">{fmt(x.average)}</td>
                    <td class="whitespace-nowrap text-muted-foreground max-sm:hidden">{fmtDate(x.last)}</td>
                    <td class="text-right tabular-nums">{fmt(x.total)}</td>
                  </tr>
                  {#if isOpen}
                    <tr><td colspan="6" class="pb-3"><MerchantDetail name={x.name} /></td></tr>
                  {/if}
                {/if}
              {/each}
            </tbody>
          </table>
        </div>
        {#if d.count > d.merchants.length}<p class="mt-3 text-sm text-muted-foreground">The top {d.merchants.length} of {d.count} merchants.</p>{/if}
      {:else}
        <p class="py-6 text-center text-sm text-muted-foreground">No spending in this period.</p>
      {/if}
    </Card.Content>
  </Card.Root>
{/if}
