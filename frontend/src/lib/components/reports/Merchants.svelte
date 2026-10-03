<script lang="ts">
  import { api } from "$lib/api";
  import { catLook } from "$lib/categories.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt0, fmtDate, plural } from "$lib/format";
  import { cn } from "$lib/utils";
  import { Report } from "./chart.svelte";
  import { dayBefore } from "./look";
  import MerchantDetail from "./MerchantDetail.svelte";
  import { rangeDates, rangeOptions, reportState as st, type RangeKey } from "./state.svelte";
  import Status from "./Status.svelte";
  import type { MerchantsReport } from "./types";

  // Merchants: where the money went, biggest first. Open one for its months and transactions. The search runs on the
  // server, so it finds a merchant past the top 100 too. The search and what's open stay when you come back to the tab.
  const report = new Report(() => {
    const { start, end } = rangeDates(st.range);
    const qs = new URLSearchParams({ start, end });
    if (st.merchantQ.trim()) qs.set("q", st.merchantQ.trim());
    return api<MerchantsReport>(`/api/reports/merchants?${qs}`);
  });
  let q = $state(st.merchantQ);
  let timer: ReturnType<typeof setTimeout> | undefined;
  function search(v: string) {
    q = v;
    clearTimeout(timer);
    timer = setTimeout(() => { st.merchantQ = q; report.load(); }, 250);
  }
  function clearSearch() { clearTimeout(timer); q = ""; st.merchantQ = ""; report.load(); }
  $effect(() => () => clearTimeout(timer));
  const open = $derived(new Set(st.merchantsOpen));
  const toggle = (name: string) => {
    st.merchantsOpen = open.has(name) ? st.merchantsOpen.filter((n) => n !== name) : [...st.merchantsOpen, name];
  };
  const top = $derived(report.data?.merchants[0]?.total || 1);
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <Segmented label="Period" value={st.range} options={rangeOptions} onchange={(v) => { st.range = v as RangeKey; st.merchantsOpen = []; report.load(); }} />
  <Input type="search" value={q} oninput={(e) => search(e.currentTarget.value)} placeholder="Find a merchant" aria-label="Find a merchant" class="max-w-[260px]" />
</div>

{#if !report.data || report.error}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const d = report.data}
  <div aria-busy={report.loading} class={cn("transition-opacity", report.loading && "opacity-60")}>
    <Card.Root>
      <Card.Content>
        {#if d.merchants.length}
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-muted-foreground [&>th]:pb-2 [&>th]:font-medium [&>th+th]:pl-4 [&>th+th]:whitespace-nowrap">
                <th>Merchant</th><th class="max-lg:hidden">Usual category</th><th class="text-right">Visits</th>
                <th class="text-right max-lg:hidden">Average</th><th class="max-lg:hidden">Last</th><th class="text-right">Money out</th>
              </tr>
            </thead>
            <tbody>
              {#each d.merchants as x (x.name)}
                {@const isOpen = open.has(x.name)}
                <tr class={cn("relative border-t hover:bg-muted/50 [&>td]:py-2.5 [&>td+td]:pl-4", isOpen && "bg-muted/50")}>
                  <td class="w-full max-w-0 min-w-28">
                    <button class="block max-w-full cursor-pointer truncate text-left outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                      title={x.name} aria-expanded={isOpen} onclick={() => toggle(x.name)}>{x.name}</button>
                    <span class="mt-1 block h-1 rounded-sm bg-(--flow-out) opacity-55" style:width={`${Math.max(2, (x.total / top) * 100)}%`}></span>
                  </td>
                  <td class="max-w-44 truncate text-muted-foreground max-lg:hidden" title={x.category}>{catLook(x.category).icon} {x.category}</td>
                  <td class="text-right tabular-nums">{x.count}</td>
                  <td class="text-right text-muted-foreground tabular-nums max-lg:hidden">{fmt0(x.average)}</td>
                  <td class="whitespace-nowrap text-muted-foreground max-lg:hidden">{fmtDate(x.last)}</td>
                  <td class="text-right whitespace-nowrap tabular-nums">{fmt0(x.total)}</td>
                </tr>
                {#if isOpen}
                  <tr><td colspan="6" class="pb-3"><MerchantDetail name={x.name} period={{ from: d.start, to: dayBefore(d.end) }} /></td></tr>
                {/if}
              {/each}
            </tbody>
          </table>
          <div class="mt-3 flex flex-wrap items-baseline justify-between gap-2 border-t pt-3 text-sm text-muted-foreground">
            <span>{d.count > d.merchants.length ? `The top ${d.merchants.length} of ${d.count} merchants` : plural(d.count, "merchant")}</span>
            <span>Total <span class="font-medium text-foreground tabular-nums">{fmt0(d.total)}</span></span>
          </div>
        {:else if st.merchantQ.trim()}
          <p class="py-6 text-center text-sm text-muted-foreground">No merchants match ·
            <Button variant="link" size="sm" class="h-auto px-0 py-0 phone:min-h-11" onclick={clearSearch}>Clear search</Button></p>
        {:else}
          <p class="py-6 text-center text-sm text-muted-foreground">No spending in this period.</p>
        {/if}
      </Card.Content>
    </Card.Root>
  </div>
{/if}
