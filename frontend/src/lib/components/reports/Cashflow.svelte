<script lang="ts">
  import { api } from "$lib/api";
  import MonthPicker, { shiftMonth } from "$lib/components/MonthPicker.svelte";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt0, fmtSigned0, monthLabel, pct, thisMonth } from "$lib/format";
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
  // It shows this month until you pick another, and moves on with the calendar while it's open (a tab left open over
  // the 1st), unless you've picked one. The month picker shows the month asked for, so a failed load can't leave it
  // on the one before.
  const follow = () => {
    if (st.month && (st.monthPicked || st.month === thisMonth())) return false;
    st.month = thisMonth(); st.monthPicked = false;
    return true;
  };
  follow();
  const report = new Report(() => api<Cashflow>(`/api/cashflow?month=${st.month}`));
  $effect(() => {
    const check = () => { if (follow()) report.load(); };
    const timer = setInterval(check, 60_000);
    document.addEventListener("visibilitychange", check);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", check); };
  });
  function pick(m: string) {
    st.month = m;
    st.monthPicked = m !== thisMonth();
    report.load();
  }
  const shift = (n: number) => pick(shiftMonth(st.month!, n));
  const share = (v: number, t: number) => (t > 0 ? pct(v / t) : "");
  const signed = (n: number) => fmtSigned0(n).replace(/^\+/, "");
</script>

<MonthPicker class="mb-4" month={st.month!} onchange={pick} />
{#if !report.data || report.error}
  <Status error={report.error} retry={() => report.load()} what={`${monthLabel(st.month!)}`} />
{:else}
  {@const cf = report.data}
  {@const label = monthLabel(cf.month)}
  {@const short = label.split(" ")[0]}
  {@const empty = !cf.income.length && !cf.spending.length}
  {@const open = cf.month >= thisMonth()}
  <!-- Whole dollars that add up: money in and out rounded, what's left over the difference of the two. -->
  {@const tin = Math.round(cf.total_in)}
  {@const tout = Math.round(cf.total_out)}
  {@const left = tin - tout}
  {@const bad = left < 0 && !open}
  {@const verdict = open ? (left >= 0 ? `Left over so far in ${short}` : `Spent more than came in so far in ${short}`)
    : left >= 0 ? "Left over" : "Spent more than came in"}
  <div aria-busy={report.loading} class={cn("transition-opacity", report.loading && "opacity-60")}>
    {#if empty}
      <Card.Root>
        <Card.Content>
          <p class="py-6 text-center text-sm text-muted-foreground">Nothing in {open ? `${short} yet` : label} ·
            <Button variant="link" size="sm" class="h-auto px-0 py-0 phone:min-h-11" onclick={() => shift(-1)}>Go back a month</Button></p>
        </Card.Content>
      </Card.Root>
    {:else}
      <section class="mb-6">
        <div class={cn("text-[15px]", bad ? "font-semibold text-destructive" : "text-muted-foreground")}>{verdict}</div>
        <div class={cn("text-[44px] leading-none font-extrabold tracking-[-0.04em] tabular-nums md:text-[56px]", bad && "text-destructive")}>{fmt0(Math.abs(left))}</div>
        {#if tin > 0}<p class="mt-2 text-[15px] text-muted-foreground">Savings rate {pct(left / tin)}</p>{/if}
        <StatStrip class="mt-5" items={[
          { label: "Money in", value: fmt0(tin) },
          { label: "Money out", value: fmt0(tout), sub: "not card payments or transfers" },
        ]} />
      </section>
      <Card.Root>
        <Card.Header><Card.Title>{short} cash flow</Card.Title></Card.Header>
        <Card.Content>
          <Sankey {cf} monthName={short} />
          <details class="group mt-2" open={isPhone()}>
            <summary class="flex w-fit cursor-pointer list-none items-center gap-1.5 text-sm text-muted-foreground select-none hover:text-foreground phone:min-h-11 [&::-webkit-details-marker]:hidden">
              <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Show as table
            </summary>
            <table class="mt-2 w-full max-w-xl text-sm">
              {#snippet head(title: string)}
                <tr class="text-left text-xs text-muted-foreground [&>th]:pb-1 [&>th]:pt-3 [&>th]:font-medium"><th>{title}</th><th class="text-right">{title === "Money in" ? "Amount" : ""}</th><th class="text-right">{title === "Money in" ? "Share" : ""}</th></tr>
              {/snippet}
              {#snippet row(name: string, v: number, sh: string, indent = "")}
                <tr class="border-t [&>td]:py-1.5">
                  <td class={cn("w-full max-w-0", indent && "pl-7")}><span class="block truncate" title={indent ? `${indent} > ${name}` : name}>{#if indent}<span class="text-muted-foreground">{indent} &gt;</span> {/if}{name}</span></td>
                  <td class="pl-3 text-right whitespace-nowrap tabular-nums">{fmt0(v)}</td><td class="pl-3 text-right text-muted-foreground tabular-nums">{sh}</td>
                </tr>
              {/snippet}
              <tbody>
                {@render head("Money in")}
                {#each cf.income as n (n.name)}{@render row(n.name, n.value, share(n.value, cf.total_in))}{/each}
                {@render head("Money out")}
                {#each cf.spending as n (n.name)}
                  {@render row(n.name, n.value, share(n.value, cf.total_out))}
                  {#each n.children as k (k.name)}{@render row(k.name, k.value, share(k.value, cf.total_out), n.name)}{/each}
                {/each}
                <tr class="border-t font-semibold [&>td]:py-1.5"><td>{open ? "Left over so far" : "Left over"}</td><td class="pl-3 text-right whitespace-nowrap tabular-nums">{signed(left)}</td><td></td></tr>
              </tbody>
            </table>
          </details>
        </Card.Content>
      </Card.Root>
    {/if}
  </div>
{/if}
