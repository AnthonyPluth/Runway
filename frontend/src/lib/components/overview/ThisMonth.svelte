<script lang="ts" module>
  import type { BudgetMonth } from "$lib/components/budget/types";
  import type { TxList } from "$lib/components/transactions/types";

  // This month so far: spending against the same point last month, the budgets closest to their limit, and the
  // latest transactions.
  interface Pace { month: string; prev_month: string; this: number[]; last: number[]; spent: number; last_same_point: number; last_total: number }
  // What was last on screen, drawn at once when Overview is drawn afresh (after a change) until the new numbers arrive.
  let last: [Pace, BudgetMonth, TxList] | null = null;
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { catLook } from "$lib/categories.svelte";
  import CatIcon from "$lib/components/CatIcon.svelte";
  import Logo from "$lib/components/Logo.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { showTransactions } from "$lib/filters.svelte";
  import { barWidth, fmt, fmt0, fmtDate, monthShort, thisMonth } from "$lib/format";
  import { cn } from "$lib/utils";

  const month = thisMonth();
  const fresh = Promise.all([
    api<Pace>("/api/month_pace"),
    api<BudgetMonth>(`/api/budget?month=${month}`),
    api<TxList>("/api/transactions?limit=5&ignored=0"),   // what you marked Ignore stays off Overview, as on Transactions
  ]);
  let data = $state.raw(last ?? fresh);
  fresh.then((r) => { data = last = r; }, () => {});
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
  <div class="h-72 animate-pulse rounded-2xl bg-card"></div>
{:then [pace, budget, latest]}
  {@const diff = pace.spent - pace.last_same_point}
  {@const budgets = budget.categories.filter((c) => c.depth === 0 && c.budget && c.budget > 0)
    .map((c) => ({ ...c, pct: c.spent / (c.available || c.budget!) })).sort((a, b) => b.pct - a.pct).slice(0, 4)}
  {@const c = chart(pace)}
  <div class="flex min-w-0 flex-col gap-6">
    <Group title="This month">
      <div class="px-4 pt-3 pb-2">
        <div class="text-[13px] text-muted-foreground">Spent so far in {monthShort(pace.month)}</div>
        <div class="text-[28px] font-semibold tracking-tight tabular-nums">{fmt(pace.spent)}</div>
        <p class={cn("text-[13px] font-medium tabular-nums", Math.abs(diff) < 1 ? "text-muted-foreground" : diff > 0 ? "text-amber-400" : "text-emerald-400")}>
          {#if Math.abs(diff) < 1}About the same as this point in {monthShort(pace.prev_month)}
          {:else}{diff > 0 ? "▲" : "▼"} {fmt0(Math.abs(diff))} {diff > 0 ? "more" : "less"} than this point in {monthShort(pace.prev_month)}{/if}
          <span class="font-normal text-muted-foreground"> · {monthShort(pace.prev_month)} total {fmt0(pace.last_total)}</span>
        </p>
        <div class="mt-2"><LineChart xs={c.xs} series={c.series} labels zero height={180} fmtY={fmt0} fmtTip={fmt} table={false} /></div>
      </div>
    </Group>

    {#if budgets.length}
    <Group title="Budgets" inset="3.75rem">
      {#snippet action()}<a href="#budget" class="text-[13px] text-primary">See all</a>{/snippet}
      {#each budgets as b (b.name)}
        <button type="button" class="cell" onclick={() => showTransactions({ category: b.name, month, scope: "budget" })}>
          <CatIcon name={b.name} size={32} />
          <span class="min-w-0 flex-1">
            <span class="flex items-baseline justify-between gap-2 text-[15px]">
              <span class="truncate">{b.name}</span>
              <span class={cn("tabular-nums", b.pct > 1 ? "font-medium text-destructive" : "text-muted-foreground")}>{Math.round(b.pct * 100)}%</span>
            </span>
            <span class="mt-1.5 block h-1 overflow-hidden rounded-full bg-muted">
              <span class={cn("block h-full rounded-full", b.pct > 1 && "bg-destructive")} style:width={barWidth(b.pct)}
                style:background={b.pct > 1 ? undefined : catLook(b.name).color}></span>
            </span>
            <span class="mt-1 block text-[13px] text-muted-foreground tabular-nums">
              {b.pct > 1 ? `${fmt0(b.spent - (b.available ?? b.budget!))} over` : `${fmt0((b.available ?? b.budget!) - b.spent)} left`} of {fmt0(b.available ?? b.budget)}</span>
          </span>
        </button>
      {/each}
    </Group>
    {/if}

    {#if latest.items.length}
    <Group title="Recent" inset="3.75rem">
      {#snippet action()}<a href="#transactions" class="text-[13px] text-primary">See all</a>{/snippet}
      {#each latest.items as t (t.id)}
        <div class="cell">
          {#if t.logo}
            <Logo src={t.logo} />
          {:else}<CatIcon name={t.category} size={32} />{/if}
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[15px]">{t.payee || t.description}</span>
            <span class="block text-[13px] text-muted-foreground">{fmtDate(t.posted)}{t.category ? ` · ${t.category}` : ""}</span>
          </span>
          <span class={cn("text-[15px] tabular-nums", t.amount > 0 && "text-emerald-400")}>{fmt(t.amount)}</span>
        </div>
      {/each}
    </Group>
    {/if}
  </div>
{:catch}
  <!-- The forecast is the main thing; if this part can't load, it just isn't shown. -->
{/await}
