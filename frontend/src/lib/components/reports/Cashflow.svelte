<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, fmt0, monthLabel, thisMonth } from "$lib/format";
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { Report } from "./chart.svelte";
  import Sankey from "./Sankey.svelte";
  import { reportState as st } from "./state.svelte";
  import Status from "./Status.svelte";
  import Tile from "./Tile.svelte";
  import type { Cashflow } from "./types";

  // Cash flow: one month as a Sankey, with its totals and the same numbers as a table.
  st.month ||= thisMonth();
  const report = new Report(() => api<Cashflow>(`/api/cashflow?month=${st.month}`));
  function shift(n: number) {
    const [y, m] = st.month!.split("-").map(Number);
    const d = new Date(y, m - 1 + n, 1);
    st.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    report.load();
  }
  const pct = (v: number, t: number) => (t > 0 ? `${Math.round((v / t) * 100)}%` : "");
</script>

{#if !report.data}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const cf = report.data}
  {@const label = monthLabel(cf.month)}
  {@const short = label.split(" ")[0]}
  {@const empty = !cf.income.length && !cf.spending.length}
  <div class="mb-4 inline-flex items-center gap-1 rounded-lg border p-[3px]">
    <Button variant="ghost" size="icon" class="size-8" aria-label="Previous month" onclick={() => shift(-1)}><ChevronLeft /></Button>
    <span class="min-w-36 rounded-md bg-muted px-3 py-1 text-center text-sm font-medium" aria-live="polite">{label}</span>
    <Button variant="ghost" size="icon" class="size-8" aria-label="Next month" onclick={() => shift(1)}><ChevronRight /></Button>
  </div>
  {#if report.error}<Status error={report.error} retry={() => report.load()} />{:else}
  <div class="mb-6 grid gap-4 md:grid-cols-3">
    <Tile label="Money in" value={fmt0(cf.total_in)} sub="income and refunds" />
    <Tile label="Money out" value={fmt0(cf.total_out)} sub="spending, not card payments or transfers" />
    <Tile label={cf.net >= 0 ? "Left over" : "▲ Spent more than came in"} value={fmt0(Math.abs(cf.net))} alert={cf.net < 0}
      sub={cf.total_in > 0 ? `${Math.round((cf.net / cf.total_in) * 100)}% of money in` : ""} />
  </div>
  <Card.Root>
    <Card.Header><Card.Title>{short} cash flow</Card.Title></Card.Header>
    <Card.Content>
      {#if empty}
        <p class="py-6 text-center text-sm text-muted-foreground">No transactions in {label}.</p>
      {:else}
        <Sankey {cf} monthName={short} />
        <details class="mt-2">
          <summary class="cursor-pointer text-sm text-muted-foreground">Show as table</summary>
          <div class="overflow-x-auto">
            <table class="mt-2 w-full max-w-xl text-sm">
              {#snippet head(title: string)}
                <tr class="text-left text-xs text-muted-foreground [&>th]:pb-1 [&>th]:pt-3 [&>th]:font-medium"><th>{title}</th><th class="text-right">{title === "Money in" ? "Amount" : ""}</th><th class="text-right">{title === "Money in" ? "Share" : ""}</th></tr>
              {/snippet}
              {#snippet row(name: string, v: number, share: string, indent = "")}
                <tr class="border-t [&>td]:py-1.5">
                  <td class={indent && "pl-7"}>{#if indent}<span class="text-muted-foreground">{indent} &gt;</span> {/if}{name}</td>
                  <td class="text-right tabular-nums">{fmt(v)}</td><td class="text-right text-muted-foreground tabular-nums">{share}</td>
                </tr>
              {/snippet}
              <tbody>
                {@render head("Money in")}
                {#each cf.income as n (n.name)}{@render row(n.name, n.value, pct(n.value, cf.total_in))}{/each}
                {@render head("Money out")}
                {#each cf.spending as n (n.name)}
                  {@render row(n.name, n.value, pct(n.value, cf.total_out))}
                  {#each n.children as k (k.name)}{@render row(k.name, k.value, pct(k.value, cf.total_out), n.name)}{/each}
                {/each}
                <tr class="border-t font-semibold [&>td]:py-1.5"><td>{cf.net >= 0 ? "Left over" : "Spent more than came in"}</td><td class="text-right tabular-nums">{fmt(Math.abs(cf.net))}</td><td></td></tr>
              </tbody>
            </table>
          </div>
        </details>
      {/if}
    </Card.Content>
  </Card.Root>
  {/if}
{/if}
