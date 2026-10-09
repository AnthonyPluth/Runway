<script lang="ts">
  import { loadAccounts } from "$lib/accounts";
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { catLabel, catPath, categories, loadCategories } from "$lib/categories.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import AiLog from "$lib/components/transactions/AiLog.svelte";
  import AiButton from "$lib/components/AiButton.svelte";
  import AiSuggest from "$lib/components/transactions/AiSuggest.svelte";
  import ReviewGroups from "$lib/components/transactions/ReviewGroups.svelte";
  import TxTable from "$lib/components/transactions/TxTable.svelte";
  import TxSheet from "$lib/components/transactions/TxSheet.svelte";
  import Plus from "@lucide/svelte/icons/plus";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import Upcoming from "$lib/components/transactions/Upcoming.svelte";
  import { comingUp } from "$lib/components/overview/comingUp";
  import { ruleOffer } from "$lib/components/transactions/remember.svelte";
  import { restoreTx, type Was } from "$lib/components/transactions/restore";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import type { RuleOffer, Tx, UpcomingEvent } from "$lib/components/transactions/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { amountLabel, isFiltered, KINDS, txShow } from "$lib/filters.svelte";
  import { fmtSigned, plural } from "$lib/format";
  import DateRange from "$lib/components/transactions/DateRange.svelte";
  import MoreFilters from "$lib/components/transactions/MoreFilters.svelte";
  import { syncStatus } from "$lib/nav.svelte";
  import { undoable } from "$lib/undo";
  import { undoBatched } from "$lib/undoBatch";
  import { cn } from "$lib/utils";
  import { accountName, type Overview } from "$lib/types";
  import { toast } from "svelte-sonner";
  import Search from "@lucide/svelte/icons/search";
  import X from "@lucide/svelte/icons/x";
  import { act, errMsg } from "$lib/act";
  import { ReviewMode } from "$lib/components/transactions/review.svelte";
  import ReviewBar from "$lib/components/transactions/ReviewBar.svelte";
  import { TxListing } from "$lib/components/transactions/txList.svelte";

  // Transactions (#transactions) and its To review tab (#review) share one list: same filters, same columns.
  // Changing a category saves immediately; in Review the transaction then leaves the list.
  let { page = "transactions", sub: _sub = "" }: { page?: string; sub?: string } = $props();
  // svelte-ignore state_referenced_locally
  const review = page === "review";
  const txs = new TxListing(review);
  const rv = new ReviewMode(txs, { accept: (t) => accept(t), save: (t, category) => save(t, category) });

  // What the page needs before the list: categories, accounts (for the filter) and recurring items (for ↻).
  const loadSetup = () => Promise.all([loadCategories(), loadAccounts(), api<RecurringItem[]>("/api/recurring")]);
  let setup = $state(loadSetup());
  // Upcoming (projected) items for the forecast account, and recurring charges on cards, on All only.
  // Loaded again in place after an amount is changed there; the old ones stay on screen until the new ones arrive.
  let upcoming = $state<UpcomingEvent[]>([]);
  let upcomingError = $state(false);
  function loadUpcoming() {
    if (review) return;
    api<Overview>(`/api/overview?days=${app.state?.horizon_days || 90}`).then((fc) => { upcoming = comingUp(fc, { charges: true }) as UpcomingEvent[]; upcomingError = false; },
      () => { upcoming = []; upcomingError = true; });   // not the old ones: they'd look current
  }
  loadUpcoming();

  let selecting = $state(false);

  const shownEvents = (events: UpcomingEvent[]) => {
    const a = txs.applied, q = a.q.trim().toLowerCase();
    return events.filter((e) => (!q || e.name.toLowerCase().includes(q)) && (!a.account || e.account_id === a.account) &&
      (!a.category || (a.category === "__none__" ? !e.category : catPath(e.category).includes(a.category))) &&
      (!a.from || e.date >= a.from) && (!a.to || e.date <= a.to) && (!a.min || Math.abs(e.amount) >= Number(a.min) - 0.005) &&
      (!a.max || Math.abs(e.amount) <= Number(a.max) + 0.005) && (a.kind !== "transfer") &&
      (!a.kind || (a.kind === "in" ? e.amount > 0 : e.amount < 0)));
  };

  /**
   * In Review a transaction leaves the queue as soon as you've decided (and the keyboard moves on); `send` then saves
   * it. If that fails it comes back where it was, and a toast says why. Elsewhere the row stays and updates once saved.
   * `done` gets the reply; null when it failed.
   */
  async function decide<R>(t: Tx, send: () => Promise<R>, done: (r: R) => void | Promise<void>): Promise<boolean> {
    const name = t.payee || t.description || "this transaction";
    if (!(review && txs.list)) {
      return act(async () => { await done(await send()); });
    }
    const at = txs.list.items.findIndex((x) => x.id === t.id);
    const next = at < 0 ? undefined : (txs.list.items[at + 1] ?? txs.list.items[at - 1])?.id;
    if (at >= 0) {
      txs.list.items.splice(at, 1);
      if (txs.count > 0) txs.count--;
      if (txs.list.total > 0) txs.list.total--;
      if (txs.list.items.length) rv.focusAfter(next);
    }
    try {
      await done(await send());
      if (txs.list && !txs.list.items.length) { await txs.load(); await rv.focusAfter(undefined); }
      return true;
    } catch (err) {
      if (txs.list && at >= 0 && !txs.list.items.some((x) => x.id === t.id)) {
        txs.list.items.splice(Math.min(at, txs.list.items.length), 0, t);
        txs.count++; txs.list.total++;
        if (rv.keyed) { rv.keyed = t.id; rv.focusAfter(t.id); }
      }
      toast.error(`Couldn’t save ${name}`, { description: errMsg(err) });
      return false;
    }
  }

  /** Keep the category it has and take it out of Review (whoever set it). */
  function accept(t: Tx): Promise<boolean> {
    const category = t.category ?? "";
    return decide(t, () => api<{ was: Was[] }>(`/api/transactions/${encodeURIComponent(t.id)}/accept`, { method: "POST" }), (r) => {
      undoBatched(`Accepted ${category}`, async () => { await restoreTx(r.was); await txs.load(); }, { description: t.payee || t.description || undefined });
      refreshState();
      if (!review) { t.needs_review = 0; t.category_source = "manual"; }
    });
  }

  // The category filter the list was loaded with, when it's a category: a split transaction's part in it is what shows.
  const only = $derived(txs.applied.category && txs.applied.category !== "__none__" ? txs.applied.category : "");

  // Saves a category picked for a transaction; false when it couldn't be saved (the picker then shows the saved one again).
  // The toast says what it was, so a wrong pick (or a slip of the keyboard) can be taken back; changes made one after
  // another share it ("3 changed · Undo"). Its main button offers to use the category for the merchant from now on.
  async function save(t: Tx, category: string): Promise<boolean> {
    if (t.match && only) return savePart(t, category);
    const prev = t.category ?? "";
    return decide(t, () => api<{ also_updated: number; offer_rule: RuleOffer | null; was: Was[] }>(
      `/api/transactions/${encodeURIComponent(t.id)}/category`, { method: "POST", body: { category } }), async (r) => {
      const offer = r.offer_rule ? ruleOffer(t.id, category, r.offer_rule, txs.load) : null;
      undoBatched(offer ? category : prev === category ? `Accepted ${category}` : `${prev || "Uncategorized"} → ${category}`,
        async () => { await restoreTx(r.was); await txs.load(); },
        { description: offer?.description || t.payee || t.description || undefined, also: offer?.also });
      refreshState();
      if (r.also_updated) { await txs.load(); return; }
      if (!review) {
        t.category = category; t.needs_review = 0; t.category_source = "manual";
        txs.load();   // the sum above the list (and a filter it may have left) follow
      }
    });
  }

  // Only the part shown changes (its order's items, when an order split it); the whole transaction takes the category
  // once every part has it. The list loads again: the part may have left the filter.
  async function savePart(t: Tx, category: string) {
    return act(async () => {
      const r = await api<{ was: Was[] }>(`/api/transactions/${encodeURIComponent(t.id)}/category`, { method: "POST", body: { category, only } });
      undoable(`${t.match!.categories.join(", ")} → ${category}`, async () => { await restoreTx(r.was); await txs.load(); },
        { description: t.payee || t.description || undefined });
      refreshState();
      await txs.load();
    });
  }

  // The sheet: a transaction's details (by id, so it shows the list's latest copy after a reload; the one it was opened
  // with if it has left the list, as in Review), or a new one to add.
  let sheetOpen = $state(false);
  let sheetTx = $state<Tx | null>(null);
  const shown = $derived(sheetTx ? (txs.list?.items.find((x) => x.id === sheetTx!.id) ?? sheetTx) : null);
  function openTx(t: Tx) { sheetTx = t; sheetOpen = true; }
  function openAdd() { sheetTx = null; sheetOpen = true; }
  // A category saved from the sheet: as from the row, and the sheet's copy follows (it may have left Review's list).
  async function saveFromSheet(t: Tx, category: string) {
    const ok = await save(t, category);
    if (ok && sheetTx?.id === t.id) sheetTx = { ...sheetTx, category, needs_review: 0, category_source: "manual", is_split: 0, splits: [] };
    return ok;
  }

  let ai = $state<AiSuggest>();
  let aiStatus = $state<"idle" | "asking" | "asked">("idle");
  let aiLog = $state<AiLog>();
</script>

<header class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <!-- Review's count sits beside the heading, not in it, so the heading reads as just "Transactions". All's is in the
       line under the filters, with what they add up to. -->
  <div class="flex items-baseline gap-2">
    <h1 bind:this={rv.heading} tabindex="-1" class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em] outline-none">Transactions</h1>
    {#if review}<span class="text-base font-normal text-muted-foreground tabular-nums">{txs.list && app.state?.connected && txs.count ? `${txs.count} to go` : ""}</span>{/if}
  </div>
  {#if !review && app.state?.connected}
    <Button variant="outline" onclick={openAdd} title="Add a transaction by hand"><Plus />Add</Button>
  {/if}
  {#if review && app.state?.has_api_key}
    {#if txs.list && txs.count > 0}
      <AiButton busy={aiStatus === "asking"} busyLabel="Asking the AI…" label={aiStatus === "asked" ? "Ask again" : "Suggest categories"}
        onclick={() => ai?.run()} />
    {/if}
  {:else if review && app.state?.connected}
    <p class="text-sm text-muted-foreground">AI suggestions: <a href="#setup/connections" class="font-medium text-primary">Settings › Connections</a></p>
  {/if}
</header>

<SubTabs label="Transactions" current={review ? "review" : "transactions"} tabs={[
  { id: "transactions", href: "#transactions", label: "All" },
  { id: "review", href: "#review", label: "To review", badge: app.state?.review_count || undefined },
]} />

{#if !app.state?.connected}
  <NotConnected title={review ? "Connect a bank to review transactions" : "Connect a bank to see your transactions"} />
{:else}
{#await setup}
  <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted"></div>
{:then [, accounts, recurring]}
  {#if review}
    <AiSuggest bind:this={ai} bind:status={aiStatus} onasked={(failed) => aiLog?.refresh(failed)} onchanged={txs.load} />
    {#if app.state?.has_api_key}<AiLog bind:this={aiLog} />{/if}
  {/if}

  <div class="mb-3 flex flex-wrap items-center gap-2">
    <div class="relative w-full sm:w-64">
      <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input type="search" placeholder="Search" aria-label="Search transactions" title="Merchant, bank’s text, category, note or amount" class="h-10 pl-8"
        value={txs.f.q} oninput={(e) => txs.search(e.currentTarget.value)} />
    </div>
    <NativeSelect aria-label="Account" class="h-10 max-sm:min-w-0 max-sm:flex-[1_1_40%] sm:max-w-48" bind:value={txs.f.account} onchange={txs.load}>
      <option value="">All accounts</option>
      {#each accounts.filter((a) => a.kind !== "investment") as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </NativeSelect>
    <NativeSelect aria-label="Category" class="h-10 max-sm:min-w-0 max-sm:flex-[1_1_40%] sm:max-w-52" bind:value={txs.f.category} onchange={txs.load}>
      <option value="">All categories</option>
      <option value="__none__">Uncategorized</option>
      {#each categories.list as c (c.name)}<option value={c.name}>{c.icon ? `${c.icon}  ` : ""}{catLabel(c)}</option>{/each}
    </NativeSelect>
    <DateRange from={txs.f.from} to={txs.f.to} note={txs.f.scope === "budget" ? "Budget accounts" : ""} onchange={txs.setDates} />
    <MoreFilters kind={txs.f.kind} min={txs.f.min} max={txs.f.max} onchange={txs.setMore} />
    {#if txs.f.kind}{@render chip(KINDS.find((k) => k.value === txs.f.kind)?.label ?? txs.f.kind, "Show every kind", () => txs.setMore({ kind: "" }))}{/if}
    {#if txs.f.min || txs.f.max}{@render chip(amountLabel(txs.f.min, txs.f.max), "Any amount", () => txs.setMore({ min: "", max: "" }))}{/if}
    {#if isFiltered(txs.f)}
      <Button variant="link" size="sm" class="h-auto px-1 py-0" onclick={txs.clearFilters}>Clear filters</Button>
    {/if}
  </div>

  {#if review}<ReviewBar {rv} />{/if}

  {#if !review && txs.list}
    <!-- How many, and what they add up to (as the day totals count: transfers aren't money in or out). Not up to date
         after a failed reload, so it's dimmed then. -->
    <p class={cn("mb-4 text-sm text-muted-foreground tabular-nums", txs.refreshError && "opacity-50")} data-summary>
      {plural(txs.list.total, "transaction")} · <span title={txs.applied.kind === "transfer" ? undefined : "Transfers not counted"}>{fmtSigned(txs.list.sum ?? 0)} net</span>
      {#if txs.ignoredCount !== 0 || txShow.ignored}
        · {txs.ignoredCount == null ? "Ignored" : `${txs.ignoredCount} ignored`} <button type="button" class="cursor-pointer font-medium text-primary"
          aria-expanded={txShow.ignored} aria-label={`${txShow.ignored ? "Hide" : "Show"} ignored transactions`}
          onclick={() => { txShow.ignored = !txShow.ignored; txs.load(); }}>{txShow.ignored ? "Hide" : "Show"}</button>
      {/if}
    </p>
  {/if}

  {#if !review}
    {#if upcomingError}
      <p class="mb-3 text-sm text-muted-foreground" data-testid="upcoming-failed">Couldn’t load what’s coming up.
        <Button variant="link" size="sm" class="h-auto p-0" onclick={loadUpcoming}>Retry</Button></p>
    {/if}
    <Upcoming events={shownEvents(upcoming)} oneAccount={!!txs.applied.account} onchanged={loadUpcoming} />
  {/if}

  {#if txs.refreshError && txs.list}
    <div role="alert" class="mb-3 flex flex-wrap items-center gap-x-2 rounded-lg bg-warning/10 px-3 py-2 text-sm">
      <span>Couldn’t refresh</span><span class="text-muted-foreground">· {txs.refreshError}</span>
      <Button variant="link" size="sm" class="ml-auto h-auto p-0" onclick={txs.load}>Retry</Button>
    </div>
  {/if}
  {#if txs.listError && !txs.list}
    <Card.Root><Card.Content>
      <p class="text-sm">Something went wrong: {txs.listError}</p>
      <Button class="mt-3" variant="outline" onclick={txs.load}>Try again</Button>
    </Card.Content></Card.Root>
  {:else if !txs.list}
    <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted" role="status" aria-busy="true"><span class="sr-only">Loading…</span></div>
  {:else if !txs.list.items.length}
    {@const sync = syncStatus(app.state).text}
    <Card.Root><Card.Content class="py-6 text-center text-sm text-muted-foreground">
      {#if txs.filtered}
        <p bind:this={rv.caughtUp} tabindex="-1" class="outline-none">No transactions match these filters.</p>
        <Button class="mt-3" variant="outline" onclick={txs.clearFilters}>Clear filters</Button>
      {:else if review}
        <p bind:this={rv.caughtUp} tabindex="-1" class="outline-none">All caught up.</p>
        <p class="mt-1"><a href="#transactions" class="font-medium text-primary">See all transactions</a> or check back after the next sync.</p>
      {:else}
        <p bind:this={rv.caughtUp} tabindex="-1" class="outline-none">No transactions yet. The first sync brings in months of history.</p>
        {#if sync}<p class="mt-1 text-xs">{sync}</p>{/if}
      {/if}
    </Card.Content></Card.Root>
  {:else}
    {#if review && rv.grouped}
      <ReviewGroups items={txs.list.items} onapplied={rv.groupApplied} onchanged={txs.load} />
    {:else}
      {#key txs.loads}
        <TxTable items={txs.list.items} total={txs.list.total} {review} {recurring} {only} family={txs.list.family} oneAccount={!!txs.applied.account} every={txs.everyFilter}
          bind:selecting focused={rv.keyed} onsave={save} onaccept={accept} onchanged={txs.load} onmore={txs.more} onopen={openTx} />
      {/key}
    {/if}
  {/if}

  <TxSheet bind:open={sheetOpen} t={shown} {accounts} account={txs.applied.account} {recurring} family={txs.list?.family} onsave={saveFromSheet} onchanged={txs.load}
    onpatched={(x) => { if (sheetTx && x.id === sheetTx.id) sheetTx = { ...sheetTx, ...x }; }} />

  {#if app.state?.logodev_configured}
    <p class="mt-3 text-xs text-muted-foreground"><a class="underline underline-offset-4 hover:text-foreground" href="https://logo.dev" target="_blank" rel="noopener">Logos provided by Logo.dev</a></p>
  {/if}
{:catch err}
  <Card.Root><Card.Content>
    <p class="text-sm">Something went wrong: {err.message}</p>
    <Button class="mt-3" variant="outline" onclick={() => (setup = loadSetup())}>Try again</Button>
  </Card.Content></Card.Root>
{/await}
{/if}

<svelte:window onkeydown={rv.onkey} />

{#snippet chip(label: string, clear: string, onclear: () => void)}
  <span class="inline-flex h-8 items-center gap-1 rounded-full bg-primary/15 pr-1 pl-3 text-sm text-primary">
    {label}
    <button type="button" class="flex size-6 cursor-pointer items-center justify-center rounded-full hover:bg-primary/20" aria-label={clear} title={clear}
      onclick={onclear}><X class="size-3.5" /></button>
  </span>
{/snippet}
