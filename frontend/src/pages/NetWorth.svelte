<script lang="ts">
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import { pct as pctChange } from "$lib/components/investments/numbers";
  import AccountPanel, { type PanelAccount } from "$lib/components/networth/AccountPanel.svelte";
  import AssetPanel from "$lib/components/networth/AssetPanel.svelte";
  import EquityView from "$lib/components/networth/EquityView.svelte";
  import RefreshFailed from "$lib/components/networth/RefreshFailed.svelte";
  import RetirementView from "$lib/components/networth/RetirementView.svelte";
  import InvestmentsView from "$lib/components/investments/InvestmentsView.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import { valueSource } from "$lib/components/networth/homeValues";
  import type { NetWorth, NwGroup } from "$lib/components/networth/types";
  import { Button } from "$lib/components/ui/button";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmtDate, fmtSigned0, nb, pct, shortMoney } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import { toast } from "svelte-sonner";
  import { act, errMsg } from "$lib/act";

  let { sub = "" }: { sub?: string } = $props();

  // The page's data. Loading again (after an edit) keeps the old numbers on screen until the new ones come; if that
  // fails, they stay under a "Couldn't refresh" line with Retry.
  let d = $state.raw<NetWorth | null>(null);
  let error = $state<string | null>(null);
  async function load() {
    try { d = await api<NetWorth>("/api/networth"); error = null; }
    catch (err) { error = errMsg(err); }
  }
  load();

  // The hero's range: it sets both the change figure and how much of the history the chart shows. A range with no data
  // (`change` is null) can't be picked; if the chosen one has none, the first that does stands in. Labelled as on
  // Investments (1M, 3M, 1Y); the API's keys are 30d, 90d and 1y.
  type RangeKey = "30d" | "90d" | "1y";
  const RANGES: readonly (readonly [RangeKey, string, string, number])[] = [["30d", "1M", "month", 30], ["90d", "3M", "3 months", 90], ["1y", "1Y", "year", 365]];
  let picked = $state<RangeKey>("30d");
  const range = $derived(d?.change[picked] != null ? picked : RANGES.find(([k]) => d?.change[k] != null)?.[0] ?? picked);
  const ch = $derived(d?.change[range]);
  const rangeLabel = $derived(RANGES.find(([k]) => k === range)![2]);
  // The change as a share of where it started ("+2.9%"); none when it started at or below zero, where a share means nothing.
  const chPct = $derived(d && ch != null && d.net - ch > 0 ? pctChange(ch / (d.net - ch)) : null);
  // The change is measured from the latest snapshot at least that old, and snapshots are only saved on days the page is
  // opened: when that one is more than a few days older than the range, say since when it really is.
  const changeFrom = $derived.by(() => {
    const s = d?.change_since?.[range];
    if (!d || !s) return null;
    const span = Math.round((Date.parse(`${d.today}T00:00:00Z`) - Date.parse(`${s}T00:00:00Z`)) / 864e5);
    if (span - RANGES.find(([k]) => k === range)![3] <= 3) return null;
    return fmtDate(s, s.slice(0, 4) === d.today.slice(0, 4) ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
  });
  // The chart starts where the change is measured from (the snapshot `change_since` names, which can be older than the
  // range), so the line and the figure above it cover the same days.
  const points = $derived.by(() => {
    if (!d) return [];
    const from = new Date(`${d.today}T00:00:00Z`);
    from.setUTCDate(from.getUTCDate() - RANGES.find(([k]) => k === range)![3]);
    const cutoff = d.change_since?.[range] ?? from.toISOString().slice(0, 10);
    const inRange = d.history.filter((h) => h.date >= cutoff);
    return inRange.length >= 2 ? inRange : d.history;
  });
  // Pointing at the chart (or stepping along it with the arrow keys) shows that day's figure in the headline.
  let scrub = $state<number | null>(null);
  const scrubbed = $derived(scrub != null ? points[scrub] ?? null : null);
  const fullDate = (s: string) => fmtDate(s, { month: "short", day: "numeric", year: "numeric" });

  // Groups with something in them: an asset group at $0 isn't in the bar, and debts that are all $0 (cards paid
  // off) leave "Nothing owed" rather than a $0.00 heading.
  const owesAnything = (g: NwGroup) => g.items.some((i) => Math.abs(i.value) >= 0.005);
  const assetGroups = $derived(d?.groups.filter((g) => g.side === "asset" && g.total > 0) ?? []);
  const liabilities = $derived(d?.groups.filter((g) => g.side === "liability" && owesAnything(g)) ?? []);
  // Each kind of asset keeps its color (in the bar, its key and before its heading) whatever else there is.
  const GROUP_COLOR: Record<string, number> = { cash: 1, investments: 2, equity: 3, home: 4, vehicle: 5, other: 6 };
  const colorOf = (key: string) => `var(--nw-${GROUP_COLOR[key] ?? 6})`;
  const KIND: Record<string, string> = { checking: "Checking", savings: "Savings", credit: "Credit card", loan: "Loan", investment: "Investment" };
  const kindLabel = (k: string) => KIND[k] ?? (k ? k[0].toUpperCase() + k.slice(1) : "Account");

  async function setLeftOut(id: string, out: boolean) {
    await api(`/api/accounts/${encodeURIComponent(id)}`, { method: "POST", body: { networth_hidden: out ? 1 : 0 } });
    await load();
  }
  // Leave an account out of net worth (or bring it back). It stays everywhere else; only these totals and the history from
  // today on change.
  async function leaveOut(id: string, name: string, out: boolean) {
    await act(async () => {
      await setLeftOut(id, out);
      if (out) undoable(`${name} is left out of net worth`, async () => { await setLeftOut(id, false); });
      else toast(`${name} is counted again`);
    });
  }

  // The "Not counted" footnote under the breakdown: a total when there are many, and a list to bring them back.
  // The total only when they're all assets or all debts; adding the two together wouldn't mean anything.
  let showExcluded = $state(false);
  const owes = (kind: string) => kind === "credit" || kind === "loan";
  const excludedTotal = $derived.by(() => {
    const ex = d?.excluded ?? [];
    return ex.length && ex.every((a) => owes(a.kind) === owes(ex[0].kind)) ? ex.reduce((sum, a) => sum + a.balance, 0) : null;
  });

  // The side panels. Each row opens one: an asset (or "add" one of a kind) or an account. What they show is looked up in
  // the page's data by id, so it stays current after a save.
  let assetOpen = $state(false), acctOpen = $state(false);
  let assetId = $state<number | null>(null), addKind = $state("home"), acctId = $state<string | null>(null);
  const panelAsset = $derived(assetId == null ? null : d?.assets_list.find((a) => a.id === assetId) ?? null);
  const panelAcct = $derived.by((): PanelAccount | null => {
    if (!d || acctId == null) return null;
    for (const g of d.groups) {
      const i = g.items.find((it) => it.type === "account" && String(it.id) === acctId);
      if (i) return { id: acctId, name: i.name, org: i.org ?? null, balance: i.value, as_of: i.as_of, synced: i.synced, counted: true };
    }
    const x = d.excluded.find((a) => a.id === acctId);
    return x ? { id: x.id, name: x.name, org: x.org, balance: x.balance, counted: false } : null;
  });
  const openAsset = (id: number) => { assetId = id; assetOpen = true; };
  const openAdd = (kind: string) => { assetId = null; addKind = kind; assetOpen = true; };
  const openAccount = (id: string) => { acctId = id; acctOpen = true; };

  // Asset-type groups (Real estate, Vehicles, Other assets) can be added to; any group can be folded away.
  const ADDABLE: Record<string, string> = { home: "home", vehicle: "vehicle", other: "other" };
  let folded = $state<Record<string, boolean>>({});
  const hasAssetItems = $derived(!!d?.assets_list.length);
</script>

{#snippet makeup()}
<div class="flex h-3 gap-0.5 overflow-hidden rounded-full bg-muted" role="img"
        aria-label={`Share of assets by type: ${assetGroups.map((g) => `${g.label} ${pct(g.total / d!.assets)}`).join(", ")}`}>
        {#each assetGroups as g (g.key)}
          <span class="block h-full min-w-0.5" style:width={`${((g.total / d!.assets) * 100).toFixed(2)}%`} style:background={colorOf(g.key)}
            title={`${g.label} ${fmt0(g.total)}`}></span>
        {/each}
      </div>
      <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
        {#each assetGroups as g (g.key)}
          <span class="inline-flex items-center gap-1.5"><i class="inline-block size-2.5 rounded-[3px]" style:background={colorOf(g.key)}></i>{g.label} {pct(g.total / d!.assets)}</span>
        {/each}
      </div>
{/snippet}

{#snippet side(groups: NwGroup[])}
  {#each groups as g (g.key)}
    <div class="border-t border-border first:border-t-0">
      <div class="flex items-center justify-between gap-2 pt-3 pb-1 text-sm font-semibold">
        <span class="flex min-w-0 items-center gap-1">
          <button type="button" class="inline-flex cursor-pointer items-center gap-1 rounded-sm max-md:min-h-10 max-md:min-w-10" aria-expanded={!folded[g.key]} aria-label={`${g.label}, ${folded[g.key] ? "expand" : "collapse"}`}
            onclick={() => (folded[g.key] = !folded[g.key])}><ChevronDown class={cn("size-4 text-muted-foreground transition-transform", folded[g.key] && "-rotate-90")} /></button>
          {#if g.side === "asset" && g.total > 0}<i class="mr-0.5 inline-block size-2.5 shrink-0 rounded-[3px]" style:background={colorOf(g.key)} aria-hidden="true" data-color={g.key}></i>{/if}
          {#if g.key === "equity"}<a href="#networth/equity" class="underline-offset-4 hover:underline">{g.label}</a>{:else}{g.label}{/if}
          {#if ADDABLE[g.key]}<button type="button" class="ml-1 cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground max-md:min-h-10 max-md:px-2" aria-label={`Add to ${g.label}`}
            onclick={() => openAdd(ADDABLE[g.key])}>+ Add</button>{/if}
        </span>
        <span class="tabular-nums">{fmt(g.total)}</span>
      </div>
      {#if !folded[g.key]}
        <ul>
          {#each g.items as i (`${i.type}:${i.id}`)}
            <li>
              {#snippet row()}
                <span class="min-w-0 py-1.5 pr-3 pl-6 text-left">
                  <span class="block">{#if i.type === "account"}<AcctLabel id={String(i.id)} name={i.name} />{:else}{i.name}{/if}</span>
                  <span class="block text-xs text-muted-foreground">
                    {#if i.type === "account"}{i.org ?? ""}
                    {:else if i.type === "equity"}Vested{i.as_of ? ` · share price as of ${fmtDate(i.as_of)}` : ""}{i.source === "carta" ? " · from Carta" : ""}
                    {:else}{valueSource(i.source)} · {i.as_of ? fmtDate(i.as_of) : ""}{/if}{#if i.equity != null && i.loan} · {fmt(i.equity)} equity after {i.loan.name}{/if}
                  </span>
                </span>
                <span class="py-1.5 text-right text-sm tabular-nums">{fmt(i.value)}</span>
              {/snippet}
              {#if i.type === "equity"}
                <a href="#networth/equity" class="flex w-full items-start justify-between gap-3 rounded-md text-sm hover:bg-muted/50" aria-label={`${i.name}, in the Equity view`}>{@render row()}</a>
              {:else}
                <button type="button" class="flex w-full cursor-pointer items-start justify-between gap-3 rounded-md text-sm hover:bg-muted/50" aria-label={`${i.name}, ${fmt(i.value)}`}
                  onclick={() => (i.type === "asset" ? openAsset(Number(i.id)) : openAccount(String(i.id)))}>{@render row()}</button>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/each}
{/snippet}

<div class="mb-4 flex items-center gap-1">
  <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Net worth</h1>
</div>
<SubTabs label="Net worth" current={sub === "investments" || sub === "equity" || sub === "retirement" ? sub : "summary"} tabs={[
  { id: "summary", label: "Summary", href: "#networth" },
  { id: "investments", label: "Investments", href: "#networth/investments" },
  { id: "equity", label: "Equity", href: "#networth/equity" },
  { id: "retirement", label: "Retirement", href: "#networth/retirement" },
]} />

{#if sub === "equity"}
  <EquityView />
{:else if sub === "retirement"}
  <RetirementView />
{:else if sub === "investments"}
  <InvestmentsView />
{:else if error && !d}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !d}
  <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
{:else}
{#if !app.state?.connected && !hasAssetItems}
  <NotConnected title="Connect a bank to track your net worth"
    text="Runway adds up your accounts and keeps the history as it grows. The first sync brings in your balances."
    secondary={{ label: "Add an asset by hand", onclick: () => openAdd("home") }} />
{:else}
  <!-- One unboxed hero: the number, its change over the chosen range, assets and liabilities, and the history chart. -->
  <section class="mb-8">
    {#if error}<RefreshFailed {error} onretry={load} />{/if}
    <div class="sr-only">Net worth</div>
    <div class="text-[44px] leading-none font-extrabold tracking-[-0.04em] tabular-nums md:text-[56px]" data-testid="nw-headline">{fmt0(scrubbed ? scrubbed.net : d.net)}</div>
    {#if d.history.length >= 2}
      <div class="mt-2 flex min-h-8 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {#if scrubbed}
          <p class="text-[15px] text-muted-foreground" data-testid="nw-change">on {fullDate(scrubbed.date)}</p>
        {:else if ch != null}
          <p class={cn("text-[15px] tabular-nums", ch >= 0.5 ? "text-good" : ch <= -0.5 ? "text-loss" : "text-muted-foreground")} data-testid="nw-change">
            {fmtSigned0(ch)}{chPct ? ` (${chPct})` : ""} <span class="text-muted-foreground">{changeFrom ? `since ${changeFrom}` : `in the last ${rangeLabel}`}</span>
          </p>
        {:else}<span></span>{/if}
        <Segmented label="Change over" value={range} onchange={(v) => (picked = v as RangeKey)}
          options={RANGES.map(([value, label]) => ({ value, label, disabled: d!.change[value] == null }))} />
      </div>
    {/if}
    <StatStrip class="mt-5" items={[
      { label: "Assets", value: fmt0(d.assets), sub: assetGroups.map((g) => g.label).join(" · ") },
      { label: "Liabilities", value: fmt0(d.liabilities), sub: liabilities.map((g) => g.label).join(" · ") || "nothing owed" },
    ]} />
    {#if d.history.length >= 2}
      <div class="mt-5">
        <LineChart xs={points.map((h) => h.date)} height={220} fmtY={shortMoney} fmtTip={fmt} onpoint={(i) => (scrub = i)}
          series={[{ name: "Net worth", values: points.map((h) => h.net), cls: "s-main", area: true }]} />
      </div>
    {:else}
      <p class="mt-5 text-sm text-muted-foreground" data-testid="nw-no-history">History builds up each day you open Runway</p>
    {/if}
  </section>

  <Card.Root class="mb-6">
    <Card.Header><Card.Title>What makes it up</Card.Title></Card.Header>
    <Card.Content>
      {@render makeup()}
      <div class="mt-4 grid gap-6 lg:grid-cols-2">
        <div class="min-w-0"><h3 class="mb-1 font-semibold">Assets</h3>{@render side(d.groups.filter((g) => g.side === "asset"))}
          {#if !hasAssetItems}
            <p class="mt-2 text-sm text-muted-foreground">Add a home, vehicle or other asset · <button type="button" class="cursor-pointer font-medium text-foreground underline underline-offset-2" onclick={() => openAdd("home")}>Add an asset</button></p>
          {/if}
        </div>
        <div class="min-w-0"><h3 class="mb-1 font-semibold">Liabilities</h3>
          {#if liabilities.length}{@render side(liabilities)}{:else}<p class="text-sm text-muted-foreground">Nothing owed</p>{/if}
        </div>
      </div>
      {#if d.excluded.length}
        <div class="mt-4 border-t border-border pt-3 text-sm text-muted-foreground">
          <p>
            Not counted: {#if d.excluded.length > 3}{d.excluded.length} accounts{excludedTotal != null ? ` (${fmt0(excludedTotal)})` : ""}{:else}{d.excluded.map((a) => nb(`${a.name} ${fmt0(a.balance)}`)).join(" · ")}{/if}
            · <button type="button" class="cursor-pointer font-medium text-foreground underline underline-offset-2" aria-expanded={showExcluded}
              onclick={() => (showExcluded = !showExcluded)}>{showExcluded ? "Hide" : "Show"}</button>
          </p>
          {#if showExcluded}
            <ul class="mt-1 divide-y text-sm text-foreground">
              {#each d.excluded as a (a.id)}
                <li class="flex items-center justify-between gap-3 py-2">
                  <span><button type="button" class="cursor-pointer underline-offset-2 hover:underline" onclick={() => openAccount(a.id)}><AcctLabel id={a.id} name={a.name} /></button><span class="text-xs text-muted-foreground">{a.org ? ` · ${a.org}` : ""} · {kindLabel(a.kind)}</span></span>
                  <span class="flex items-center gap-3">
                    <span class="tabular-nums">{fmt(a.balance)}</span>
                    <Button size="sm" variant="outline" aria-label={`Count ${a.name} in net worth again`} onclick={() => leaveOut(a.id, a.name, false)}>Count it again</Button>
                  </span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
    </Card.Content>
  </Card.Root>
{/if}

  <AssetPanel bind:open={assetOpen} a={panelAsset} kind={addKind} {d} onchanged={load} />
  <AccountPanel bind:open={acctOpen} acct={panelAcct} onchange={(counted) => panelAcct && leaveOut(panelAcct.id, panelAcct.name, !counted)} />
{/if}
