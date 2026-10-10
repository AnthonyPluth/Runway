<script lang="ts">
  import { api } from "$lib/api";
  import BarChart from "$lib/components/investments/BarChart.svelte";
  import HandTracked from "$lib/components/investments/HandTracked.svelte";
  import HoldingsTable from "$lib/components/investments/HoldingsTable.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import LiveDot from "$lib/components/investments/LiveDot.svelte";
  import { applyLiveQuotes, livePrices } from "$lib/components/investments/live";
  import { gainCls, pct, pctAbs, signed } from "$lib/components/investments/numbers";
  import { inv } from "$lib/components/investments/state.svelte";
  import type { AllocKey, Investments, LiveQuotes, Quote } from "$lib/components/investments/types";
  import RefreshFailed from "$lib/components/networth/RefreshFailed.svelte";
  import { plaidProblem } from "$lib/components/settings/plaidErrors";
  import type { PlaidStatus } from "$lib/components/settings/types";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import StatStrip, { type Stat, type StatTone } from "$lib/components/StatStrip.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { barWidth, fmt, fmt0, fmtDateTime, pct as share, plural, shortMoney } from "$lib/format";
  import { cn } from "$lib/utils";
  import Check from "@lucide/svelte/icons/check";
  import Info from "@lucide/svelte/icons/info";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { tick } from "svelte";
  import { errMsg } from "$lib/act";

  // The page's data. A redraw (another period, a saved edit) keeps the old numbers on screen until the new ones come;
  // if it fails they stay, under a "Couldn't refresh" line. The period is only kept once its numbers have come, so
  // the picker (`shownPeriod`, which shows a choice while it loads) goes back if they don't.
  let status = $state<PlaidStatus | null>(null);
  let d = $state<Investments | null>(null);
  let error = $state<string | null>(null);
  let busy = $state(false);
  let live = $state<LiveQuotes | null>(null);
  let shownPeriod = $state(inv.period);

  async function load(period = inv.period) {
    busy = true;
    try {
      // Both at once: the portfolio doesn't depend on the status to be read (it only reads; with no investment
      // accounts it is an empty answer from the database, no outside calls), the status only says whether to show it.
      // So the answer is dropped when there are no accounts, and a failure of it counts only when there are.
      const portfolio = api<Investments>(`/api/investments?period=${period}`);
      portfolio.catch(() => { /* handled below, or not needed when there are no investment accounts */ });
      const s = await api<PlaidStatus>("/api/plaid/status");
      const data = s.inv_accounts ? await portfolio : null;
      status = s; d = data; error = null; live = null;
      inv.period = period;
    } catch (err) { error = errMsg(err); }
    shownPeriod = inv.period;
    busy = false;
  }
  load();

  // Live prices while this page is open: they re-price the holdings and the totals. Leaving the page stops them.
  $effect(() => {
    if (!d) return;
    const data = d, quotes: Record<string, Quote> = {};
    return livePrices((l) => {
      Object.assign(quotes, l.quotes);
      applyLiveQuotes(data, quotes, l.market);
      live = l;
    });
  });

  const perf = $derived(d?.performance ?? {});
  // A gain or loss is only colored green or coral (`up`/`down`); the warning tones are kept for what needs a look.
  const tone = (x: number | null | undefined): StatTone | undefined => (x == null || x === 0 ? undefined : x > 0 ? "up" : "down");
  const beat = $derived(perf.benchmark_return != null && perf.return != null ? perf.return - perf.benchmark_return : null);
  const stats = $derived.by((): Stat[] => {
    if (!d) return [];
    return [
      { label: "Total value", value: fmt0(d.total), sub: `${plural(d.accounts.filter((a) => !a.hidden).length, "account")} · ${plural(d.holdings.length, "holding")}` },
      { label: "Today", value: d.day_change == null ? "—" : signed(d.day_change), tone: tone(d.day_change), sub: d.day_change_pct == null ? undefined : `${pct(d.day_change_pct, 2)} since the last close` },
      { label: "Total gain", value: signed(d.unrealized_gain), tone: tone(d.unrealized_gain),
        sub: d.cost_basis && d.unrealized_gain != null ? `${pct(d.unrealized_gain / d.cost_basis)} on ${fmt0(d.cost_basis)} cost basis` : undefined },
      { label: `Return · ${inv.period}`, value: pct(perf.return), tone: tone(perf.return),
        sub: `S&P 500 ${pct(perf.benchmark_return)}${beat == null ? "" : pctAbs(beat) === pctAbs(0) ? " · level with it" : beat > 0 ? ` · ahead by ${pctAbs(beat)}` : ` · behind by ${pctAbs(beat)}`}` },
    ];
  });
  const lastSync = $derived([status?.last_inv_sync, status?.simplefin_last_sync].filter(Boolean).sort().pop());
  const synced = $derived(lastSync ? fmtDateTime(new Date(lastSync)) : "never");
  // Holdings more than two days old are worth a sync: the line says so in the warning color.
  const syncStale = $derived(!lastSync || Date.now() - new Date(lastSync).getTime() > 2 * 864e5);
  const liveTime = $derived(live ? new Date(live.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "");

  // The charts start where the chosen period does.
  const charts = $derived.by(() => {
    if (!d) return null;
    const h = d.history;
    const s = Math.max(0, h.dates.findIndex((x) => x >= (perf.start || h.dates[0])));
    const sl = <T,>(arr: T[]) => arr.slice(s);
    const t0 = 1 + (h.twr[s] || 0), b0 = h.benchmark[s];
    return {
      dates: sl(h.dates),
      value: sl(h.value), invested: sl(h.invested),
      twr: sl(h.twr).map((r) => (1 + r) / t0 - 1),
      bench: sl(h.benchmark).map((b) => (b == null || b0 == null ? null : (1 + b) / (1 + b0) - 1)),
    };
  });
  const incomeLabels = $derived(d?.income.months.map((m) => {
    const [yy, mm] = m.split("-").map(Number);
    return new Date(yy, mm - 1, 1).toLocaleDateString("en-US", { month: "short" }) + (mm === 1 ? ` ${String(yy).slice(2)}` : "");
  }) ?? []);

  function setPeriod(p: string) { void load(p); }
  // "2 holdings need a cost basis": sort those to the top and go to them.
  async function showMissing(e: MouseEvent) {
    e.preventDefault();
    inv.sort = { key: "gain", dir: 1 };
    await tick();
    document.getElementById("inv-holdings")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const ALLOC: [AllocKey, string][] = [["asset_class", "Asset class"], ["account", "Account"], ["sector", "Sector"], ["holding", "Top holdings"]];
</script>

{#if error && !status}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={() => load()}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !status}
  <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
{:else if !status.inv_accounts || !d}
  <!-- Nothing to show yet: the same card as a page without a bank (NotConnected), for investment accounts -->
  <Card.Root class="mx-auto mt-6 max-w-xl text-center md:mt-10" data-testid="getting-started">
    <Card.Header>
      <img src="/logo.svg" alt="" width="40" height="40" class="mx-auto mb-2" />
      <Card.Title class="text-xl">Connect a brokerage or retirement account</Card.Title>
      <Card.Description>Its holdings, returns and allocation show up here. For one that only sends a balance, you can enter its holdings by hand.</Card.Description>
    </Card.Header>
    <Card.Content class="flex flex-col items-center gap-3">
      <Button href="#setup/connections">Connect an investment account</Button>
    </Card.Content>
  </Card.Root>
{:else}
  {#if error}<RefreshFailed {error} onretry={() => load(shownPeriod)} />{/if}
  <div class="mb-6 flex flex-wrap items-center justify-end gap-3">
    <div class="flex flex-wrap items-center gap-3">
      <span class={cn("inline-flex items-center gap-1.5 text-sm", live?.market === "open" ? "text-foreground/80" : !live && syncStale ? "text-warning" : "text-muted-foreground")} role="status"
        title={!live || live.market === "open" ? "Stock and ETF prices update as they move while the market is open" : undefined} data-testid="holdings-updated">
        {#if !live}Holdings updated {synced}
        {:else if live.market === "open"}<LiveDot /> Live prices
        {:else}Market closed · latest prices as of {liveTime}{/if}
      </span>
      <Segmented label="Period" bind:value={shownPeriod} onchange={setPeriod} class={busy ? "opacity-70" : ""}
        options={["1M", "3M", "YTD", "1Y", "2Y"].map((p) => ({ value: p, label: p }))} />
    </div>
  </div>

  {#each status.items.filter((i) => i.error) as i (i.item_id)}
    {@const problem = plaidProblem(i.error!)}
    <Alert.Root variant="destructive" class="mb-3">
      <TriangleAlert />
      <Alert.Description><p>{i.institution_name || "A connection"}: {problem.text}. <a class="font-medium underline underline-offset-4" href="#setup/connections">{problem.reconnect ? "Reconnect in Settings" : "See Settings"}</a></p></Alert.Description>
    </Alert.Root>
  {/each}

  <StatStrip class="mb-6" items={stats} />
  {#if d.cost_missing}
    <p class="-mt-3 mb-6 text-sm text-muted-foreground">
      <a href="#inv-holdings" class="font-medium text-foreground underline underline-offset-4" onclick={showMissing}>{d.cost_missing} holding{d.cost_missing === 1 ? "" : "s"} ({fmt0(d.cost_missing_value)}) need a cost basis</a>
    </p>
  {/if}

  <Card.Root class="mb-6">
    <Card.Header>
      <Card.Title>Value</Card.Title>
      <!-- Live prices re-price the totals and holdings; the charts are the last sync's until the next one -->
      {#if live}<Card.Action><Badge variant="secondary" class="font-normal text-muted-foreground" title="Live prices update the totals and holdings above; the charts update with the next sync">Charts as of last sync</Badge></Card.Action>{/if}
    </Card.Header>
    <Card.Content>
      {#if charts}
        <LineChart xs={charts.dates} height={260} fmtY={shortMoney} fmtTip={fmt} estimateUntil={d.history.estimated_before} table="sr" series={[
          { name: "Value", values: charts.value, cls: "s-main", area: true },
          { name: "Net deposits", values: charts.invested, cls: "s-muted", step: true },
        ]} />
        <h3 class="mt-6 mb-2 font-semibold">Return vs S&amp;P 500</h3>
        <LineChart xs={charts.dates} height={200} zero fmtY={(v) => pct(v, 0)} fmtTip={(v) => pct(v, 2)} estimateUntil={d.history.estimated_before} table="sr" series={[
          { name: "Your portfolio", values: charts.twr, cls: "s-main" },
          { name: "S&P 500", values: charts.bench, cls: "s-alt" },
        ]} />
      {/if}
      <div class="mt-4 overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-xs text-muted-foreground"><th class="pb-1 text-left font-medium">Period</th>
            {#each Object.keys(d.periods) as p (p)}<th class="pb-1 pl-2 text-right font-medium sm:pl-3">{p}</th>{/each}</tr></thead>
          <tbody class="tabular-nums [&_td]:py-1.5 [&_td:not(:first-child)]:pl-2 sm:[&_td:not(:first-child)]:pl-3 [&_td]:whitespace-nowrap">
            <tr class="border-t border-border"><td>Return</td>{#each Object.entries(d.periods) as [k, p] (k)}<td class={cn("text-right", gainCls(p.return))}>{pct(p.return)}</td>{/each}</tr>
            <tr class="border-t border-border"><td>S&amp;P 500</td>{#each Object.entries(d.periods) as [k, p] (k)}<td class="text-right text-muted-foreground">{pct(p.benchmark_return)}</td>{/each}</tr>
            <tr class="border-t border-border"><td>Gain</td>{#each Object.entries(d.periods) as [k, p] (k)}<td class={cn("text-right text-muted-foreground", gainCls(p.gain))}>{signed(p.gain)}</td>{/each}</tr>
          </tbody>
        </table>
      </div>
    </Card.Content>
  </Card.Root>

  <Card.Root class="mb-6">
    <Card.Header><Card.Title>Holdings</Card.Title></Card.Header>
    <Card.Content>
      <HoldingsTable holdings={d.holdings} onchanged={load} />
    </Card.Content>
  </Card.Root>

  <div class="mb-6 grid gap-6 lg:grid-cols-2">
    <Card.Root>
      <Card.Header class="max-sm:has-data-[slot=card-action]:grid-cols-1">
        <Card.Title>Allocation</Card.Title>
        <Card.Action class="min-w-0 max-w-full overflow-x-auto max-sm:col-start-1 max-sm:row-span-1 max-sm:row-start-2 max-sm:justify-self-start">
          <Segmented label="Allocation by" value={inv.allocTab} onchange={(v) => (inv.allocTab = v as AllocKey)}
            options={ALLOC.map(([value, label]) => ({ value, label }))} />
        </Card.Action>
      </Card.Header>
      <Card.Content>
        {#if d.allocation[inv.allocTab]?.length}
          <table class="w-full text-sm">
            <tbody>
              {#each d.allocation[inv.allocTab] as a (a.name)}
                <tr class="border-t border-border first:border-t-0 [&>td]:py-2">
                  <td class="pr-3">{a.name}</td>
                  <td class="w-[45%] pr-3"><span class="block h-1.5 overflow-hidden rounded-full bg-muted"><span class="block h-full rounded-full bg-[var(--nw-1)]" style:width={barWidth(a.share)}></span></span></td>
                  <td class="pr-3 text-right tabular-nums">{share(a.share)}</td>
                  <td class="text-right text-muted-foreground tabular-nums">{fmt0(a.value)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </Card.Content>
    </Card.Root>
    <Card.Root>
      <Card.Header><Card.Title>Checks</Card.Title></Card.Header>
      <Card.Content>
        <ul>
          {#each d.xray as r (r.name)}
            <li class="flex gap-3 border-t border-border py-2 first:border-t-0">
              <span class={cn("mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-background",
                r.ok ? "bg-[var(--good)]" : r.info ? "bg-muted-foreground" : "bg-destructive")} aria-hidden="true">
                {#if r.ok}<Check class="size-3.5" />{:else if r.info}<Info class="size-3.5" />{:else}<TriangleAlert class="size-3" />{/if}
              </span>
              <div>
                <b>{r.name}</b> <span class="ml-1 text-xs text-muted-foreground">{r.ok ? "looks fine" : r.info ? "note" : "worth a look"}</span>
                <div class="text-sm text-muted-foreground">{r.detail}</div>
              </div>
            </li>
          {/each}
        </ul>
      </Card.Content>
    </Card.Root>
  </div>

  <Card.Root class="mb-6">
    <Card.Header>
      <Card.Title>Dividends &amp; interest</Card.Title>
      <Card.Description>{fmt(d.income.income_12m)} in the last 12 months · fees {fmt(d.income.fees_12m)}</Card.Description>
    </Card.Header>
    <Card.Content><BarChart labels={incomeLabels} values={d.income.income} fmtTip={fmt} /></Card.Content>
  </Card.Root>

  <HandTracked accounts={d.accounts} seen={status.simplefin_seen} onchanged={load} />
{/if}
