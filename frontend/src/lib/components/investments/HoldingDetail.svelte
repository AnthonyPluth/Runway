<script lang="ts">
  import { ApiError } from "$lib/api";
  import { errMsg } from "$lib/act";
  import { apiCall } from "$lib/contract";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import LiveDot from "$lib/components/investments/LiveDot.svelte";
  import CostBasisEditor from "$lib/components/investments/CostBasisEditor.svelte";
  import { livePrices, repriceHolding } from "$lib/components/investments/live";
  import { gainCls, pct, qty, signed } from "$lib/components/investments/numbers";
  import type { HoldingPage, Quote } from "$lib/components/investments/types";
  import RefreshFailed from "$lib/components/networth/RefreshFailed.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmtDate, pct as share } from "$lib/format";
  import { cn } from "$lib/utils";
  import ArrowLeft from "@lucide/svelte/icons/arrow-left";
  import Pencil from "@lucide/svelte/icons/pencil";
  import { onMount } from "svelte";

  // One holding: its price and how the day went, a chart of the price over a period, your position in it and where it is
  // held, from GET /api/investments/holding/<id>. Opened from a row of the Holdings list (#investments/holding/<id>).
  // Live prices re-price the page while it's open, as they do on Investments; the chart is the last sync's.
  let { id }: { id: string } = $props();

  const PERIODS = [["1M", "1M"], ["3M", "3M"], ["YTD", "YTD"], ["1Y", "1Y"], ["2Y", "2Y"], ["MAX", "All"]].map(([value, label]) => ({ value, label }));

  let d = $state<HoldingPage | null>(null);
  let missing = $state(false);           // the server doesn't know this id (sold, or the account is gone)
  let error = $state<string | null>(null);
  let busy = $state(false);
  let period = $state("1Y");
  let shownPeriod = $state("1Y");        // the picker, which shows the choice while it loads and goes back if that fails
  let editing = $state(false);
  let live = $state(false);
  // What the server said the holding and the portfolio were worth, to work out the share of the portfolio as the price moves.
  let base = { value: 0, total: 0 };

  async function load(p = period) {
    busy = true;
    try {
      const r = await apiCall<"GET /api/investments/holding/{id}">(`/api/investments/holding/${encodeURIComponent(id)}?period=${p}`);
      d = r; missing = false; error = null; period = p;
      base = { value: r.holding.value, total: r.total };
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { d = null; missing = true; error = null; }
      else error = errMsg(e);
    }
    shownPeriod = period;
    busy = false;
  }
  void load();
  onMount(() => window.scrollTo?.(0, 0));   // (the list's place is kept for the way back: HoldingsTable)

  $effect(() => {
    if (!d) return;
    const h = d.holding, quotes: Record<string, Quote> = {};
    return livePrices((l) => {
      Object.assign(quotes, l.quotes);
      const q = h.ticker ? quotes[h.ticker] : undefined;
      repriceHolding(h, q, l.market);
      if (q && h.quantity) {
        const total = base.total - base.value + h.value;
        h.allocation = total > 0 ? h.value / total : 0;
      }
      live = !!h.live;
    });
  });

  const h = $derived(d?.holding ?? null);
  const hist = $derived(d?.history ?? null);
  const symbol = $derived(h?.ticker && !h.ticker.includes(":") ? h.ticker : "");
  // Since the period began: the price now against the close then (the live price once there is one).
  const since = $derived.by(() => {
    if (!h || !hist || hist.start_price == null || h.price == null) return null;
    const change = h.price - hist.start_price;
    return { change, pct: hist.start_price ? change / hist.start_price : null };
  });
  const arrow = (x: number | null | undefined) => (x == null || x === 0 ? "" : x > 0 ? "▲" : "▼");
  const hasCost = $derived(!!h && h.gain != null);
  const editable = $derived(!!h && !(h.is_cash || h.asset_class === "Not reported"));
  const avgCost = $derived(h && hasCost && h.quantity ? h.cost_basis / h.quantity : null);
  /** A lot's gain as a fraction of what it cost, or null without a cost basis. */
  const lotGain = (value: number, cost: number | null) => (cost ? value / cost - 1 : null);
  const lotValue = (quantity: number, fallback: number) => (h?.price != null ? quantity * h.price : fallback);
  const fmtPrice = (v: number) => (v >= 1000 ? fmt(v).replace(/\.00$/, "") : fmt(v));
  const done = (changed: boolean) => { editing = false; if (changed) void load(); };
</script>

<div class="mx-auto max-w-2xl" data-testid="holding-page">
  <a href="#investments" class="-ml-2 mb-3 inline-flex min-h-10 items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50">
    <ArrowLeft class="size-4" aria-hidden="true" /> Investments
  </a>

  {#if missing}
    <Card.Root data-testid="holding-missing">
      <Card.Content class="text-center">
        <p class="font-medium">This holding isn't in your portfolio any more</p>
        <Button class="mt-3" variant="outline" href="#investments">Back to Investments</Button>
      </Card.Content>
    </Card.Root>
  {:else if error && !d}
    <Card.Root>
      <Card.Content>
        <p class="text-sm">Something went wrong: {error}</p>
        <Button class="mt-3" variant="outline" onclick={() => load()}>Try again</Button>
      </Card.Content>
    </Card.Root>
  {:else if !d || !h || !hist}
    <div class="space-y-4" aria-busy="true" aria-label="Loading">
      <div class="h-24 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
      <div class="h-64 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
    </div>
  {:else}
    {#if error}<RefreshFailed {error} onretry={() => load(shownPeriod)} />{/if}
    <header class="mb-6" data-testid="holding-header">
      <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">{symbol || h.name || "Holding"}</h1>
      {#if symbol && h.name}<p class="mt-1 text-muted-foreground">{h.name}</p>{/if}
      <p class="mt-3 flex items-center gap-2 text-4xl leading-tight font-bold tracking-[-0.03em] tabular-nums" data-testid="holding-price">
        {h.price == null ? "—" : fmt(h.price)}
        {#if live}<LiveDot class="size-[9px]" label="Live price" />{/if}
      </p>
      {#if h.day_change != null}
        <p class={cn("mt-1 text-sm font-medium tabular-nums", gainCls(h.day_change))} data-testid="holding-today">
          {#if h.day_change !== 0}<span aria-hidden="true">{arrow(h.day_change)}</span>{/if}
          {signed(h.day_change)}{#if h.day_change_pct != null}{" "}({pct(h.day_change_pct, 2)}){/if} <span class="font-normal text-muted-foreground">today</span>
        </p>
      {/if}
    </header>

    <Card.Root class="mb-6">
      <Card.Content>
        {#if hist.dates.length > 1}
          <LineChart xs={hist.dates} height={200} fmtY={fmtPrice} fmtTip={fmt} table="sr"
            series={[{ name: symbol || h.name || "Price", values: hist.prices, cls: "s-main", area: true }]} />
        {:else}
          <p class="py-10 text-center text-sm text-muted-foreground">No price history for this period</p>
        {/if}
        {#if hist.start_price != null && since}
          <p class="mt-2 text-sm text-muted-foreground tabular-nums" data-testid="holding-since">
            {fmt(hist.start_price)} on {fmtDate(hist.dates[0], { month: "short", day: "numeric", year: "numeric" })} ·
            <span class={gainCls(since.change)}>{signed(since.change)}{#if since.pct != null}{" "}({pct(since.pct)}){/if}</span> since
          </p>
        {/if}
        <Segmented label="Period" class={cn("mt-3 w-full", busy && "opacity-70")} bind:value={shownPeriod} onchange={(p) => void load(p)} options={PERIODS} />
      </Card.Content>
    </Card.Root>

    <Card.Root class="mb-6">
      <Card.Header><Card.Title>Your position</Card.Title></Card.Header>
      <Card.Content>
        <dl class="divide-y divide-border text-sm tabular-nums [&>div]:flex [&>div]:items-center [&>div]:justify-between [&>div]:gap-4 [&>div]:py-2.5" data-testid="holding-position">
          <div><dt class="text-muted-foreground">Market value</dt><dd class="font-semibold">{fmt(h.value)}</dd></div>
          <div><dt class="text-muted-foreground">Share of portfolio</dt><dd>{share(h.allocation)}</dd></div>
          <div><dt class="text-muted-foreground">Shares</dt><dd>{qty(h.quantity)}</dd></div>
          <div>
            <dt class="text-muted-foreground">Average cost</dt>
            <dd>
              {#if editable}
                <button type="button" class="group inline-flex min-h-10 cursor-pointer items-center gap-1.5 rounded-md border border-dashed border-transparent px-1.5 hover:border-border focus-visible:border-border"
                  aria-expanded={editing} title="Edit cost basis" onclick={() => (editing = !editing)}>
                  <span class={cn("underline decoration-dotted decoration-from-font underline-offset-4", !hasCost && "text-[var(--nw-1)]")}>{hasCost && avgCost != null ? fmt(avgCost) : "No cost basis"}</span>
                  <Pencil class="size-3 opacity-70" aria-hidden="true" />
                </button>
              {:else}<span class="text-muted-foreground">{hasCost && avgCost != null ? fmt(avgCost) : "—"}</span>{/if}
            </dd>
          </div>
          <div><dt class="text-muted-foreground">Today</dt>
            <dd class={gainCls(h.day_change)}>{h.day_change == null ? "—" : signed(h.day_change)}{#if h.day_change_pct != null}{" "}({pct(h.day_change_pct, 2)}){/if}</dd></div>
          <div><dt class="text-muted-foreground">Total gain</dt>
            <dd class={gainCls(h.gain)}>{h.gain == null ? "—" : signed(h.gain)}{#if h.gain_pct != null}{" "}({pct(h.gain_pct)}){/if}</dd></div>
        </dl>
        {#if editing}
          <div class="mt-2 rounded-lg bg-muted/40 p-3" data-editor><CostBasisEditor holding={h} ondone={done} /></div>
        {/if}
      </Card.Content>
    </Card.Root>

    <Card.Root class="mb-6">
      <Card.Header><Card.Title>Held in</Card.Title></Card.Header>
      <Card.Content>
        <ul data-testid="holding-accounts">
          {#each h.lots as l (l.account_id)}
            {@const value = lotValue(l.quantity, l.value)}
            {@const gain = lotGain(value, l.cost_basis)}
            <li class="flex items-center justify-between gap-3 border-t border-border py-2.5 first:border-t-0">
              <span class="min-w-0">
                <span class="block truncate font-medium">{l.account_name}</span>
                <span class="block text-xs text-muted-foreground tabular-nums">{qty(l.quantity)} shares · {l.per_share != null ? `avg ${fmt(l.per_share)}` : "No cost basis"}</span>
              </span>
              <span class="flex shrink-0 flex-col items-end text-sm tabular-nums">
                <span class="font-semibold">{fmt(value)}</span>
                <span class={cn("text-xs", gain == null ? "text-muted-foreground" : gainCls(gain))}>{gain == null ? "—" : pct(gain)}</span>
              </span>
            </li>
          {/each}
        </ul>
      </Card.Content>
    </Card.Root>

    {#if d.dividends_12m > 0}
      <Card.Root class="mb-6">
        <Card.Header>
          <Card.Title>Dividends</Card.Title>
          <Card.Description>{fmt(d.dividends_12m)} in the last 12 months</Card.Description>
        </Card.Header>
      </Card.Root>
    {/if}
  {/if}
</div>
