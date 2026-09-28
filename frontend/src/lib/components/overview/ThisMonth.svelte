<script lang="ts">
  import { api } from "$lib/api";
  import { catLook } from "$lib/categories.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import type { BudgetMonth } from "$lib/components/budget/types";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import type { TxList } from "$lib/components/transactions/types";
  import * as Card from "$lib/components/ui/card";
  import { showTransactions } from "$lib/filters.svelte";
  import { fmt, fmt0, fmtDate, monthShort, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";

  // This month so far: spending against the same point last month, the budgets closest to their limit, and the
  // latest transactions.
  interface Pace { month: string; prev_month: string; this: number[]; last: number[]; spent: number; last_same_point: number; last_total: number }

  const month = thisMonth();
  const data = Promise.all([
    api<Pace>("/api/month_pace"),
    api<BudgetMonth>(`/api/budget?month=${month}`),
    api<TxList>("/api/transactions?limit=5"),
  ]);
  const chart = (p: Pace) => {
    const [y, m] = p.month.split("-").map(Number);
    const n = new Date(y, m, 0).getDate();   // this month's days (a longer last month's final day isn't drawn)
    const label = monthShort(p.month);
    return {
      xs: Array.from({ length: n }, (_, i) => `${label} ${i + 1}`),
      series: [
        { name: monthShort(p.month), cls: "s-main" as const, area: true, values: Array.from({ length: n }, (_, i) => p.this[i] ?? null) },
        { name: monthShort(p.prev_month), cls: "s-muted" as const, values: Array.from({ length: n }, (_, i) => p.last[i] ?? null) },
      ],
    };
  };
</script>

{#await data}
  <div class="mb-6 h-72 animate-pulse rounded-xl bg-muted"></div>
{:then [pace, budget, latest]}
  {@const diff = pace.spent - pace.last_same_point}
  {@const budgets = budget.categories.filter((c) => c.depth === 0 && c.budget && c.budget > 0)
    .map((c) => ({ ...c, pct: c.spent / (c.available || c.budget!) })).sort((a, b) => b.pct - a.pct).slice(0, 4)}
  {@const c = chart(pace)}
  <h2 class="mt-8 mb-3 text-lg font-semibold tracking-tight">This month</h2>
  <div class="mb-6 grid gap-6 lg:grid-cols-5">
    <Card.Root class="lg:col-span-3">
      <Card.Header>
        <Card.Description>Spent so far in {monthShort(pace.month)}</Card.Description>
        <Card.Title class="text-2xl tabular-nums">{fmt(pace.spent)}</Card.Title>
        <p class={cn("text-sm tabular-nums", Math.abs(diff) < 1 ? "text-muted-foreground" : diff > 0 ? "text-amber-500" : "text-emerald-500")}>
          {#if Math.abs(diff) < 1}About the same as this point in {monthShort(pace.prev_month)}
          {:else}{diff > 0 ? "▲" : "▼"} {fmt0(Math.abs(diff))} {diff > 0 ? "more" : "less"} than this point in {monthShort(pace.prev_month)}{/if}
          <span class="text-muted-foreground"> · {monthShort(pace.prev_month)} total {fmt0(pace.last_total)}</span>
        </p>
      </Card.Header>
      <Card.Content>
        <LineChart xs={c.xs} series={c.series} labels zero height={240} fmtY={fmt0} fmtTip={fmt} table={false} />
      </Card.Content>
    </Card.Root>

    <div class="flex flex-col gap-6 lg:col-span-2">
      <Card.Root class="gap-3">
        <Card.Header><Card.Title>Budgets</Card.Title><Card.Action><a href="#budget" class="text-sm text-muted-foreground hover:text-foreground">See all</a></Card.Action></Card.Header>
        <Card.Content>
          {#if budgets.length}
            <ul class="flex flex-col gap-3">
              {#each budgets as b (b.name)}
                <li>
                  <button type="button" class="w-full cursor-pointer text-left" onclick={() => showTransactions({ category: b.name, month, scope: "budget" })}>
                    <span class="flex items-center gap-2 text-sm">
                      <CatIcon name={b.name} size={20} class="rounded-full" /><span class="truncate">{b.name}</span>
                      <span class={cn("ml-auto tabular-nums", b.pct > 1 ? "font-medium text-destructive" : "text-muted-foreground")}>{fmt0(b.spent)} of {fmt0(b.available ?? b.budget)}</span>
                    </span>
                    <span class="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-muted">
                      <span class={cn("block h-full rounded-full", b.pct > 1 && "bg-destructive")} style:width={`${Math.min(100, b.pct * 100)}%`}
                        style:background={b.pct > 1 ? undefined : catLook(b.name).color}></span>
                    </span>
                  </button>
                </li>
              {/each}
            </ul>
          {:else}
            <p class="text-sm text-muted-foreground">No budgets yet. <a class="font-medium text-foreground underline underline-offset-4" href="#budget">Set one</a> to see how you're tracking.</p>
          {/if}
        </Card.Content>
      </Card.Root>

      <Card.Root class="gap-3">
        <Card.Header><Card.Title>Latest transactions</Card.Title><Card.Action><a href="#transactions" class="text-sm text-muted-foreground hover:text-foreground">See all</a></Card.Action></Card.Header>
        <Card.Content>
          <ul class="flex flex-col">
            {#each latest.items as t (t.id)}
              <li class="flex items-center gap-3 border-t py-2 first:border-t-0 first:pt-0">
                <CatIcon name={t.category} size={28} class="rounded-full" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-sm font-medium">{t.payee || t.description}</span>
                  <span class="block text-xs text-muted-foreground">{fmtDate(t.posted)}{t.category ? ` · ${t.category}` : ""}</span>
                </span>
                <span class={cn("text-sm tabular-nums", t.amount > 0 && "font-semibold text-emerald-500")}>{fmt(t.amount)}</span>
              </li>
            {:else}
              <li class="text-sm text-muted-foreground">Nothing yet.</li>
            {/each}
          </ul>
        </Card.Content>
      </Card.Root>
    </div>
  </div>
{:catch}
  <!-- The forecast above is the main thing; if this part can't load, it just isn't shown. -->
{/await}
