<script lang="ts">
  import { api } from "$lib/api";
  import MonthPicker, { shiftMonth } from "$lib/components/MonthPicker.svelte";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, fmt0, monthLabel, thisMonth } from "$lib/format";
  import { isPhone } from "$lib/phone.svelte";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { Report } from "./chart.svelte";
  import Sankey from "./Sankey.svelte";
  import { reportState as st } from "./state.svelte";
  import Status from "./Status.svelte";
  import type { Cashflow } from "./types";

  // Cash flow: one month as a Sankey, with its totals and the same numbers as a table. A month that isn't over yet is
  // "so far", in muted text: on the 1st, rent is out and the paycheck isn't in, and that's no verdict on the month.
  st.month ||= thisMonth();
  const report = new Report(() => api<Cashflow>(`/api/cashflow?month=${st.month}`));
  function pick(m: string) {
    st.month = m;
    report.load();
  }
  const shift = (n: number) => pick(shiftMonth(st.month!, n));
  const pct = (v: number, t: number) => (t > 0 ? `${Math.round((v / t) * 100)}%` : "");
</script>

{#if !report.data}
  <Status error={report.error} retry={() => report.load()} />
{:else}
  {@const cf = report.data}
  {@const label = monthLabel(cf.month)}
  {@const short = label.split(" ")[0]}
  {@const empty = !cf.income.length && !cf.spending.length}
  {@const open = cf.month >= thisMonth()}
  {@const bad = cf.net < 0 && !open}
  {@const verdict = open ? (cf.net >= 0 ? `Left so far in ${short}` : `Spent more than came in so far in ${short}`)
    : cf.net >= 0 ? "Left over" : "▲ Spent more than came in"}
  <MonthPicker class="mb-4" month={cf.month} onchange={pick} />
  {#if report.error}<Status error={report.error} retry={() => report.load()} />{:else}
  <section class="mb-6">
    <div class={cn("text-[15px]", bad ? "font-semibold text-destructive" : "text-muted-foreground")}>{verdict}</div>
    <div class={cn("text-[44px] leading-none font-extrabold tracking-[-0.04em] tabular-nums md:text-[56px]", bad && "text-destructive")}>{fmt0(Math.abs(cf.net))}</div>
    {#if cf.total_in > 0}<p class="mt-2 text-[15px] text-muted-foreground">{Math.round((cf.net / cf.total_in) * 100)}% of money in</p>{/if}
    <StatStrip class="mt-5" items={[
      { label: "Money in", value: fmt0(cf.total_in), sub: "income and refunds" },
      { label: "Money out", value: fmt0(cf.total_out), sub: "spending, not card payments or transfers" },
    ]} />
  </section>
  <Card.Root>
    <Card.Header><Card.Title>{short} cash flow</Card.Title></Card.Header>
    <Card.Content>
      {#if empty}
        <p class="py-6 text-center text-sm text-muted-foreground">No transactions in {label}. Try an earlier month ·
          <Button variant="link" size="sm" class="h-auto px-0 py-0" onclick={() => shift(-1)}>Go back one month</Button></p>
      {:else}
        <Sankey {cf} monthName={short} />
        <details class="group mt-2" open={isPhone()}>
          <summary class="flex w-fit cursor-pointer list-none items-center gap-1.5 text-sm text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Show as table
          </summary>
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
                <tr class="border-t font-semibold [&>td]:py-1.5"><td>{cf.net >= 0 ? (open ? "Left so far" : "Left over") : open ? "Spent more than came in so far" : "Spent more than came in"}</td><td class="text-right tabular-nums">{fmt(Math.abs(cf.net))}</td><td></td></tr>
              </tbody>
            </table>
          </div>
        </details>
      {/if}
    </Card.Content>
  </Card.Root>
  {/if}
{/if}
