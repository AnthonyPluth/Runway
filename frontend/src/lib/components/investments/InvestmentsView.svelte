<script lang="ts">
  import { api } from "$lib/api";
  import BarChart from "$lib/components/investments/BarChart.svelte";
  import HoldingsTable from "$lib/components/investments/HoldingsTable.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import LiveDot from "$lib/components/investments/LiveDot.svelte";
  import { applyLiveQuotes, livePrices } from "$lib/components/investments/live";
  import { gainCls, pct, signed } from "$lib/components/investments/numbers";
  import { inv } from "$lib/components/investments/state.svelte";
  import type { AllocKey, Investments, LiveQuotes, Quote } from "$lib/components/investments/types";
  import type { PlaidStatus } from "$lib/components/settings/types";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { barWidth, fmt, fmt0, fmtDate, fmtDateTime, shortMoney } from "$lib/format";
  import { cn } from "$lib/utils";
  import Check from "@lucide/svelte/icons/check";
  import Info from "@lucide/svelte/icons/info";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { tick, type Snippet } from "svelte";

  // The page's data. A redraw (another period, a saved edit) keeps the old numbers on screen until the new ones come.
  let status = $state<PlaidStatus | null>(null);
  let d = $state<Investments | null>(null);
  let error = $state<string | null>(null);
  let busy = $state(false);
  let live = $state<LiveQuotes | null>(null);

  async function load() {
    busy = true;
    try {
      const s = await api<PlaidStatus>("/api/plaid/status");
      const data = s.inv_accounts ? await api<Investments>(`/api/investments?period=${inv.period}`) : null;
      status = s; d = data; error = null; live = null;
    } catch (err) { error = (err as Error).message; }
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
  const beat = $derived(perf.benchmark_return != null && perf.return != null ? perf.return - perf.benchmark_return : null);
  const synced = $derived.by(() => {
    const last = [status?.last_inv_sync, status?.simplefin_last_sync].filter(Boolean).sort().pop();
    return last ? fmtDateTime(new Date(last)) : "never";
  });
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

  function setPeriod(p: string) { inv.period = p; load(); }
  // "2 holdings need a cost basis": sort those to the top and go to them.
  async function showMissing(e: MouseEvent) {
    e.preventDefault();
    inv.sort = { key: "gain", dir: 1 };
    await tick();
    document.getElementById("inv-holdings")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const ALLOC: [AllocKey, string][] = [["asset_class", "Asset class"], ["account", "Account"], ["sector", "Sector"], ["holding", "Top holdings"]];
  const activity = $derived(d?.activity.filter((t) => !inv.activityType || t.type === inv.activityType) ?? []);
</script>

{#snippet tile(label: string, value: string, sub: Snippet, tone = "")}
  <Card.Root class="gap-2">
    <Card.Header>
      <Card.Description>{label}</Card.Description>
      <Card.Title class={cn("text-2xl tabular-nums", tone)}>{value}</Card.Title>
    </Card.Header>
    <Card.Content class="text-sm text-muted-foreground">{@render sub()}</Card.Content>
  </Card.Root>
{/snippet}

{#if error && !status}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !status}
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:else if !status.inv_accounts || !d}
  <Card.Root class="mb-6" data-testid="getting-started">
    <Card.Content class="flex flex-col gap-3">
      <p class="text-sm text-muted-foreground">Connect a brokerage or retirement account to see it here.</p>
      <div class="flex flex-wrap gap-2">
        <Button size="sm" href="#setup/connections">Connect an investment account</Button>
      </div>
    </Card.Content>
  </Card.Root>
{:else}
  <div class="mb-6 flex flex-wrap items-center justify-end gap-3">
    <div class="flex flex-wrap items-center gap-3">
      <span class={cn("inline-flex items-center gap-1.5 text-sm", live?.market === "open" ? "text-foreground/80" : "text-muted-foreground")} role="status"
        title={!live || live.market === "open" ? "Stock and ETF prices update as they move while the market is open" : undefined}>
        {#if !live}Holdings updated {synced}
        {:else if live.market === "open"}<LiveDot /> Live prices
        {:else}Market closed · latest prices as of {liveTime}{/if}
      </span>
      <Segmented label="Period" value={inv.period} onchange={setPeriod} class={busy ? "opacity-70" : ""}
        options={["1M", "3M", "YTD", "1Y", "2Y"].map((p) => ({ value: p, label: p }))} />
    </div>
  </div>

  {#each status.items.filter((i) => i.error) as i (i.item_id)}
    <Alert.Root variant="destructive" class="mb-3">
      <TriangleAlert />
      <Alert.Description><p>{i.institution_name || "A connection"} needs attention ({i.error}). <a class="font-medium underline underline-offset-4" href="#setup/connections">Reconnect in Settings</a></p></Alert.Description>
    </Alert.Root>
  {/each}

  <div class="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
    {#snippet totalSub()}{d!.accounts.filter((a) => !a.hidden).length} accounts · {d!.holdings.length} holdings{/snippet}
    {@render tile("Total value", fmt0(d.total), totalSub)}
    {#snippet todaySub()}{d!.day_change_pct == null ? "" : `${pct(d!.day_change_pct, 2)} since the last close`}{/snippet}
    {@render tile("Today", d.day_change == null ? "—" : signed(d.day_change), todaySub, gainCls(d.day_change))}
    {#snippet gainSub()}
      {d!.cost_basis ? `${pct(d!.unrealized_gain! / d!.cost_basis)} on ${fmt0(d!.cost_basis)} invested` : ""}
      {#if d!.cost_missing} · <a href="#inv-holdings" class="font-medium text-foreground underline underline-offset-4" onclick={showMissing}>{d!.cost_missing} holding{d!.cost_missing === 1 ? "" : "s"} ({fmt0(d!.cost_missing_value)}) need a cost basis</a>{/if}
    {/snippet}
    {@render tile("Total gain", d.unrealized_gain == null ? "—" : signed(d.unrealized_gain), gainSub, gainCls(d.unrealized_gain))}
    {#snippet returnSub()}S&amp;P 500 {pct(perf.benchmark_return)}{beat == null ? "" : beat >= 0 ? ` · ahead by ${pct(beat).slice(1)}` : ` · behind by ${pct(-beat).slice(1)}`}{/snippet}
    {@render tile(`Return · ${inv.period}`, pct(perf.return), returnSub, gainCls(perf.return))}
  </div>

  <Card.Root class="mb-6">
    <Card.Header><Card.Title>Value</Card.Title></Card.Header>
    <Card.Content>
      {#if charts}
        <LineChart xs={charts.dates} height={260} fmtY={shortMoney} fmtTip={fmt} estimateUntil={d.history.estimated_before} table="sr" series={[
          { name: "Value", values: charts.value, cls: "s-main", area: true },
          { name: "Net invested", values: charts.invested, cls: "s-muted", step: true },
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
            {#each Object.keys(d.periods) as p (p)}<th class="pb-1 pl-3 text-right font-medium">{p}</th>{/each}</tr></thead>
          <tbody class="tabular-nums [&_td]:py-1.5 [&_td:not(:first-child)]:pl-3 [&_td]:whitespace-nowrap">
            <tr class="border-t border-border"><td>Your return</td>{#each Object.entries(d.periods) as [k, p] (k)}<td class={cn("text-right", gainCls(p.return))}>{pct(p.return)}</td>{/each}</tr>
            <tr class="border-t border-border"><td>S&amp;P 500</td>{#each Object.entries(d.periods) as [k, p] (k)}<td class="text-right text-muted-foreground">{pct(p.benchmark_return)}</td>{/each}</tr>
            <tr class="border-t border-border"><td class="whitespace-nowrap">Gain after deposits</td>{#each Object.entries(d.periods) as [k, p] (k)}<td class={cn("text-right text-muted-foreground", gainCls(p.gain))}>{signed(p.gain)}</td>{/each}</tr>
          </tbody>
        </table>
      </div>
    </Card.Content>
  </Card.Root>

  <Card.Root class="mb-6">
    <Card.Header><Card.Title>Holdings</Card.Title></Card.Header>
    <Card.Content><HoldingsTable holdings={d.holdings} onchanged={load} /></Card.Content>
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
                  <td class="pr-3 text-right tabular-nums">{(a.share * 100).toFixed(1)}%</td>
                  <td class="text-right text-muted-foreground tabular-nums">{fmt0(a.value)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}<p class="py-6 text-center text-sm text-muted-foreground">Nothing to show.</p>{/if}
      </Card.Content>
    </Card.Root>
    <Card.Root>
      <Card.Header><Card.Title>X-ray</Card.Title></Card.Header>
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

  <Card.Root class="mb-6">
    <Card.Header>
      <Card.Title>Activity</Card.Title>
      <Card.Action>
        <NativeSelect aria-label="Kind of activity" value={inv.activityType} onchange={(e) => { inv.activityType = e.currentTarget.value; inv.activityLimit = 40; }}>
          <option value="">All activity</option>
          {#each ["buy", "sell", "cash", "fee", "transfer"] as t (t)}<option value={t}>{t}</option>{/each}
        </NativeSelect>
      </Card.Action>
    </Card.Header>
    <Card.Content>
      {#if activity.length}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-xs text-muted-foreground [&>th]:pb-1 [&>th]:font-medium [&>th:not(:first-child)]:pl-3">
              <th class="text-left">Date</th><th class="text-left">Activity</th><th class="text-left max-[700px]:hidden">Account</th>
              <th class="text-right">Shares</th><th class="text-right">Price</th><th class="text-right">Cash</th>
            </tr></thead>
            <tbody>
              {#each activity.slice(0, inv.activityLimit) as t (t.id)}
                <tr class="border-t border-border align-top [&>td]:py-2 [&>td:not(:first-child)]:pl-3">
                  <td class="whitespace-nowrap text-muted-foreground">{fmtDate(t.date, { month: "short", day: "numeric", year: "numeric" })}</td>
                  <td>
                    <div>{t.name ?? ""}</div>
                    <div class="text-xs text-muted-foreground">{t.type ?? ""}{t.subtype && t.subtype !== t.type ? ` · ${t.subtype}` : ""}{t.ticker && !t.ticker.includes(":") ? ` · ${t.ticker}` : ""}</div>
                  </td>
                  <td class="text-muted-foreground max-[700px]:hidden">{t.account_name}</td>
                  <td class="text-right tabular-nums">{t.quantity ? t.quantity.toLocaleString("en-US", { maximumFractionDigits: 4 }) : ""}</td>
                  <td class="text-right text-muted-foreground tabular-nums">{t.price ? fmt(t.price) : ""}</td>
                  <td class={cn("text-right whitespace-nowrap tabular-nums", gainCls(-(t.amount ?? 0)))}>{t.amount ? signed(-t.amount) : ""}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if activity.length > inv.activityLimit}
          <Button variant="link" size="sm" class="mt-2 px-0" onclick={() => (inv.activityLimit += 100)}>Show more ({activity.length - inv.activityLimit})</Button>
        {/if}
      {:else}<p class="py-6 text-center text-sm text-muted-foreground">No activity.</p>{/if}
    </Card.Content>
  </Card.Root>
{/if}
