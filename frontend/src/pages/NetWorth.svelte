<script lang="ts">
  import { api } from "$lib/api";
  import AcctLabel from "$lib/components/AcctLabel.svelte";
  import LineChart from "$lib/components/investments/LineChart.svelte";
  import { signed } from "$lib/components/investments/numbers";
  import AssetCard from "$lib/components/networth/AssetCard.svelte";
  import AssetForm from "$lib/components/networth/AssetForm.svelte";
  import EquityCard from "$lib/components/networth/EquityCard.svelte";
  import { valueSource } from "$lib/components/networth/homeValues";
  import type { Asset, NetWorth, NwGroup } from "$lib/components/networth/types";
  import { Button } from "$lib/components/ui/button";
  import StatStrip from "$lib/components/StatStrip.svelte";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt, fmt0, fmtDate, nb, pct, shortMoney } from "$lib/format";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";

  let { sub: _sub = "" }: { sub?: string } = $props();

  // The page's data. Loading again (after an edit) keeps the old numbers on screen until the new ones come.
  let d = $state.raw<NetWorth | null>(null);
  let error = $state<string | null>(null);
  let version = $state(0);
  async function load() {
    try { d = await api<NetWorth>("/api/networth"); error = null; version++; }
    catch (err) { error = (err as Error).message; }
  }
  load();

  // The hero's range: it sets both the change figure and how much of the history the chart shows. A range with no data
  // (`change` is null) can't be picked; if the chosen one has none, the first that does stands in.
  const RANGES = [["30d", "30 days", 30], ["90d", "90 days", 90], ["1y", "1 year", 365]] as const;
  let picked = $state<"30d" | "90d" | "1y">("30d");
  const range = $derived(d?.change[picked] != null ? picked : RANGES.find(([k]) => d?.change[k] != null)?.[0] ?? picked);
  const ch = $derived(d?.change[range]);
  const rangeLabel = $derived(RANGES.find(([k]) => k === range)![1]);
  const points = $derived.by(() => {
    if (!d) return [];
    const days = RANGES.find(([k]) => k === range)![2];
    const from = new Date(`${d.today}T00:00:00Z`);
    from.setUTCDate(from.getUTCDate() - days);
    const cutoff = from.toISOString().slice(0, 10);
    const inRange = d.history.filter((h) => h.date >= cutoff);
    return inRange.length >= 2 ? inRange : d.history;
  });
  const since = $derived(d?.first_snapshot ? fmtDate(d.first_snapshot, { month: "short", day: "numeric", year: "numeric" }) : null);
  const assetGroups = $derived(d?.groups.filter((g) => g.side === "asset" && g.total > 0) ?? []);
  const liabilities = $derived(d?.groups.filter((g) => g.side === "liability") ?? []);

  // Leave an account out of net worth (or bring it back). It stays everywhere else; only these totals and the history from
  // today on change.
  async function leaveOut(id: string, name: string, out: boolean) {
    try {
      await api(`/api/accounts/${encodeURIComponent(id)}`, { method: "POST", body: { networth_hidden: out ? 1 : 0 } });
      await load();
      toast(out ? `${name} is left out of net worth` : `${name} is counted again`,
        out ? { action: { label: "Undo", onClick: () => leaveOut(id, name, false) } } : undefined);
    } catch (err) { toast.error((err as Error).message); }
  }

  // The "Not counted" footnote under the breakdown: a total when there are many, and a list to bring them back.
  // The total only when they're all assets or all debts; adding the two together wouldn't mean anything.
  let showExcluded = $state(false);
  const owes = (kind: string) => kind === "credit" || kind === "loan";
  const excludedTotal = $derived.by(() => {
    const ex = d?.excluded ?? [];
    return ex.length && ex.every((a) => owes(a.kind) === owes(ex[0].kind)) ? ex.reduce((sum, a) => sum + a.balance, 0) : null;
  });

  // The asset form: null when closed, "new" to add one, or the asset being edited.
  let form = $state<Asset | "new" | null>(null);
  const closeForm = (changed: boolean) => { form = null; if (changed) load(); };
</script>

{#snippet side(groups: NwGroup[])}
  <table class="w-full text-sm">
    <tbody>
      {#each groups as g (g.key)}
        <tr class="border-t border-border first:border-t-0"><td class="pt-3 pb-1 font-semibold">{g.label}</td><td class="pt-3 pb-1 text-right font-semibold tabular-nums">{fmt(g.total)}</td></tr>
        {#each g.items as i (`${i.type}:${i.id}`)}
          <tr class="align-top">
            <td class="py-1.5 pr-3 pl-6">
              {#if i.type === "account"}<AcctLabel id={String(i.id)} name={i.name} />{:else}{i.name}{/if}
              <div class="text-xs text-muted-foreground">
                {#if i.type === "account"}{i.org ?? ""}{#if i.org} · {/if}<button type="button" class="cursor-pointer underline underline-offset-2 hover:text-foreground"
                  aria-label={`Leave ${i.name} out of net worth`} onclick={() => leaveOut(String(i.id), i.name, true)}>Leave out</button>
                {:else if i.type === "equity"}Vested{i.as_of ? ` · share price as of ${fmtDate(i.as_of)}` : ""}{i.source === "carta" ? " · from Carta" : ""}
                {:else}{valueSource(i.source)} · {i.as_of ? fmtDate(i.as_of) : ""}{/if}{#if i.equity != null && i.loan} · {fmt(i.equity)} equity after {i.loan.name}{/if}
              </div>
            </td>
            <td class="py-1.5 text-right tabular-nums">{fmt(i.value)}</td>
          </tr>
        {/each}
      {/each}
    </tbody>
  </table>
{/snippet}

{#if error && !d}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {error}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content>
  </Card.Root>
{:else if !d}
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:else}
  <h1 class="mb-6 text-[34px] leading-tight font-bold tracking-tight">Net worth</h1>

  <!-- One unboxed hero: the number, its change over the chosen range, assets and liabilities, and the history chart. -->
  <section class="mb-8">
    <div class="sr-only">Net worth</div>
    <div class="text-[44px] leading-none font-bold tracking-tight tabular-nums md:text-[56px]">{fmt0(d.net)}</div>
    {#if d.history.length >= 2}
      <div class="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p class={cn("text-[15px] tabular-nums", ch != null && ch > 0 ? "text-emerald-500" : "text-muted-foreground")}>
          {ch != null ? `${signed(ch)} in the last ${rangeLabel}` : "Not enough history for a change yet"}
        </p>
        <Segmented label="Change over" value={range} onchange={(v) => (picked = v as typeof picked)}
          options={RANGES.map(([value]) => ({ value, label: value, disabled: d!.change[value] == null }))} />
      </div>
    {:else}
      <p class="mt-2 text-[15px] text-muted-foreground">
        {since ? `Tracking since ${since}. ` : ""}The chart fills in as the days go by.
      </p>
    {/if}
    <StatStrip class="mt-5" items={[
      { label: "Assets", value: fmt0(d.assets), sub: assetGroups.map((g) => g.label).join(" · ") },
      { label: "Liabilities", value: fmt0(d.liabilities), sub: liabilities.map((g) => nb(`${g.label} ${fmt0(g.total)}`)).join(" · ") || "nothing owed" },
    ]} />
    {#if d.history.length >= 2}
      <div class="mt-5">
        <LineChart xs={points.map((h) => h.date)} height={220} fmtY={shortMoney} fmtTip={fmt}
          series={[{ name: "Net worth", values: points.map((h) => h.net), cls: "s-main", area: true }]} />
      </div>
    {/if}
  </section>

  <Card.Root class="mb-6">
    <Card.Header><Card.Title>What makes it up</Card.Title></Card.Header>
    <Card.Content>
      <div class="flex h-3 gap-0.5 overflow-hidden rounded-full bg-muted" role="img"
        aria-label={`Share of assets by type: ${assetGroups.map((g) => `${g.label} ${pct(g.total / d!.assets)}`).join(", ")}`}>
        {#each assetGroups as g, i (g.key)}
          <span class="block h-full min-w-0.5" style:width={`${((g.total / d.assets) * 100).toFixed(2)}%`} style:background={`var(--nw-${(i % 6) + 1})`}
            title={`${g.label} ${fmt0(g.total)}`}></span>
        {/each}
      </div>
      <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
        {#each assetGroups as g, i (g.key)}
          <span class="inline-flex items-center gap-1.5"><i class="inline-block size-2.5 rounded-[3px]" style:background={`var(--nw-${(i % 6) + 1})`}></i>{g.label} {pct(g.total / d.assets)}</span>
        {/each}
      </div>
      <div class="mt-4 grid gap-6 lg:grid-cols-2">
        <div><h3 class="mb-1 font-semibold">Assets</h3>{@render side(d.groups.filter((g) => g.side === "asset"))}</div>
        <div><h3 class="mb-1 font-semibold">Liabilities</h3>
          {#if liabilities.length}{@render side(liabilities)}{:else}<p class="text-sm text-muted-foreground">Nothing owed</p>{/if}
        </div>
      </div>
      {#if d.excluded.length}
        <div class="mt-4 border-t border-border pt-3 text-sm text-muted-foreground">
          <p>
            Not counted: {#if d.excluded.length > 3}{d.excluded.length} accounts{excludedTotal != null ? ` (${fmt0(excludedTotal)})` : ""}{:else}{d.excluded.map((a) => nb(`${a.name} ${fmt0(a.balance)}`)).join(" · ")}{/if}
            · <button type="button" class="cursor-pointer font-medium text-foreground underline underline-offset-2" aria-expanded={showExcluded}
              onclick={() => (showExcluded = !showExcluded)}>Manage</button>
          </p>
          {#if showExcluded}
            <p class="mt-2 text-xs">These accounts stay in the rest of Runway (transactions, the forecast, Investments) but aren’t counted here.</p>
            <ul class="mt-1 divide-y text-sm text-foreground">
              {#each d.excluded as a (a.id)}
                <li class="flex items-center justify-between gap-3 py-2">
                  <span><AcctLabel id={a.id} name={a.name} /><span class="text-xs text-muted-foreground">{a.org ? ` · ${a.org}` : ""} · {a.kind}</span></span>
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

  <EquityCard {version} refresh={load} />

  <Card.Root>
    <Card.Header>
      <Card.Title>Home, vehicles and other assets</Card.Title>
      <Card.Action><Button size="sm" onclick={() => (form = "new")}>Add an asset</Button></Card.Action>
    </Card.Header>
    <Card.Content>
      {#if form}
        {#key form}<AssetForm a={form === "new" ? null : form} {d} onclose={closeForm} />{/key}
      {/if}
      {#if d.assets_list.length}
        <div class="grid gap-3">
          {#each d.assets_list as a (a.id)}
            <AssetCard {a} d={d} onedit={() => (form = a)} onchanged={load} />
          {/each}
        </div>
      {:else}<p class="py-6 text-center text-sm text-muted-foreground">No assets yet.</p>{/if}
    </Card.Content>
  </Card.Root>
{/if}
