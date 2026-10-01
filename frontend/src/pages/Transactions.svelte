<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import AssumptionsLink from "$lib/components/AssumptionsLink.svelte";
  import { catLabel, catParentOf, categories, loadCategories } from "$lib/categories.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import AiLog from "$lib/components/transactions/AiLog.svelte";
  import AiSuggest from "$lib/components/transactions/AiSuggest.svelte";
  import RememberBar from "$lib/components/transactions/RememberBar.svelte";
  import TxTable from "$lib/components/transactions/TxTable.svelte";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import Upcoming from "$lib/components/transactions/Upcoming.svelte";
  import { comingUp } from "$lib/components/overview/comingUp";
  import { askRemember } from "$lib/components/transactions/remember.svelte";
  import { restoreTx, type Was } from "$lib/components/transactions/restore";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import type { RuleOffer, Tx, TxList, UpcomingEvent } from "$lib/components/transactions/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { clearAll, isFiltered, txFilters, txShow, type TxFilters } from "$lib/filters.svelte";
  import { monthLabel } from "$lib/format";
  import { syncStatus } from "$lib/nav.svelte";
  import { undoable } from "$lib/undo";
  import { accountName, type Account, type Overview } from "$lib/types";
  import { tick } from "svelte";
  import { toast } from "svelte-sonner";
  import Search from "@lucide/svelte/icons/search";
  import X from "@lucide/svelte/icons/x";

  // Transactions (#transactions) and its To review tab (#review) share one list: same filters, same columns.
  // Changing a category saves immediately; in Review the transaction then leaves the list.
  let { page = "transactions", sub: _sub = "" }: { page?: string; sub?: string } = $props();
  // svelte-ignore state_referenced_locally
  const review = page === "review";
  const f: TxFilters = review ? txFilters.review : txFilters.transactions;

  // What the page needs before the list: categories, accounts (for the filter) and recurring items (for ↻).
  const setup = Promise.all([loadCategories(), api<Account[]>("/api/accounts"), api<RecurringItem[]>("/api/recurring")]);
  // Upcoming (projected) items for the forecast account, on All only.
  const upcoming: Promise<UpcomingEvent[]> = review ? Promise.resolve([])
    : api<Overview>(`/api/overview?days=${app.state?.horizon_days || 90}`).then((fc) => comingUp(fc) as UpcomingEvent[]).catch(() => []);

  let list = $state<TxList | null>(null);
  let listError = $state("");
  let count = $state(0);             // the count in the heading (in Review, goes down as you categorize)
  let loads = $state(0);             // a new search starts the table afresh (no leftover ticks); a reload after a change keeps it
  let applied = $state<TxFilters>({ ...f });   // the filters the list (and Upcoming) was last loaded with
  let appliedIgnored = txShow.ignored;
  let ignoredCount = $state(0);      // what's marked Ignore, under the same search and filters (All only)
  let seq = 0;

  async function countIgnored() {
    if (review) return;
    const qs = new URLSearchParams({ q: f.q, account: f.account, category: "Ignore", month: f.month, scope: f.scope, limit: "1", offset: "0" });
    try { const r = await api<TxList>(`/api/transactions?${qs}`); ignoredCount = f.category && f.category !== "Ignore" ? 0 : r.total; }
    catch { /* the line just isn't shown */ }
  }

  async function load() {
    const mine = ++seq;
    const now = { ...f }, ignored = txShow.ignored;
    // After a change with the same filters, load as many as were showing, so the list (and where you are in it) stays.
    const same = !!list && ignored === appliedIgnored && (Object.keys(now) as (keyof TxFilters)[]).every((k) => now[k] === applied[k]);
    const qs = query(0, same ? Math.min(1000, Math.max(PAGE, list!.items.length)) : PAGE);
    try {
      const data = await api<TxList>(`/api/transactions?${qs}`);
      if (mine !== seq) return;   // a newer search has been asked for meanwhile
      // The same search again (after a change): the rows are updated where they are, so nothing redraws or jumps. A new
      // search starts the table afresh.
      applied = now; appliedIgnored = ignored; list = data; count = data.total; listError = "";
      if (!same) loads++;
      countIgnored();
    } catch (err) { if (mine === seq) listError = (err as Error).message; }
  }
  const PAGE = 100;
  load();

  function query(offset: number, limit = PAGE) {
    const qs = new URLSearchParams({ q: f.q, account: f.account, category: f.category, month: f.month, scope: f.scope,
      limit: String(limit), offset: String(offset) });
    if (review) qs.set("review", "1");
    else if (!txShow.ignored) qs.set("ignored", "0");   // what's marked Ignore stays out of All unless asked for
    return qs;
  }
  // The next page, added to the end (skipping any that shifted in since, e.g. after a sync).
  async function more() {
    if (!list) return;
    const mine = seq, have = new Set(list.items.map((t) => t.id));
    const data = await api<TxList>(`/api/transactions?${query(list.items.length)}`);
    if (mine !== seq || !list) return;
    list.items.push(...data.items.filter((t) => !have.has(t.id)));
    list.total = data.total;
  }
  let selecting = $state(false);

  const filtered = $derived(isFiltered(applied));

  let timer: ReturnType<typeof setTimeout>;
  function search(v: string) { f.q = v; clearTimeout(timer); timer = setTimeout(load, 250); }
  function clearMonth() { f.month = ""; f.scope = ""; load(); }
  function clearFilters() { clearTimeout(timer); clearAll(f); load(); }

  const shownEvents = (events: UpcomingEvent[]) => {
    const a = applied, q = a.q.trim().toLowerCase();
    return events.filter((e) => (!q || e.name.toLowerCase().includes(q)) && (!a.account || e.account_id === a.account) &&
      (!a.category || (a.category === "__none__" ? !e.category : e.category === a.category || catParentOf(e.category) === a.category)) &&
      (!a.month || e.date.startsWith(a.month)));
  };

  // After a transaction leaves the list, keyboard focus goes to the next row's category (else the row before it), or,
  // with none left, to "All caught up": without this it falls to the page and you start again from the top.
  let heading = $state<HTMLElement>(), caughtUp = $state<HTMLElement>();
  async function focusAfter(id: string | undefined) {
    await tick();
    const row = id ? [...document.querySelectorAll<HTMLElement>("[data-tx]")].find((r) => r.dataset.tx === id) : undefined;
    (row?.querySelector<HTMLElement>("select") ?? caughtUp ?? heading)?.focus();
  }

  async function save(t: Tx, category: string) {
    const prev = t.category ?? "";
    const at = list?.items.findIndex((x) => x.id === t.id) ?? -1;
    const next = at < 0 ? undefined : (list!.items[at + 1] ?? list!.items[at - 1])?.id;
    try {
      const r = await api<{ also_updated: number; offer_rule: RuleOffer | null; was: Was[] }>(
        `/api/transactions/${encodeURIComponent(t.id)}/category`, { method: "POST", body: { category } });
      if (r.offer_rule) askRemember(t.id, category, r.offer_rule, load);
      // Not a plain "Saved": what it was, so a wrong pick (or a slip of the keyboard) can be taken back.
      undoable(prev === category ? `Kept ${category}` : `${prev || "Uncategorized"} → ${category}`,
        async () => { await restoreTx(r.was); await load(); }, { description: t.payee || t.description || undefined });
      refreshState();
      if (r.also_updated) return load();
      if (review && list) {
        list.items = list.items.filter((x) => x.id !== t.id);
        if (!list.items.length) { await load(); return focusAfter(undefined); }
        if (count > 0) count--;
        if (list.total > 0) list.total--;
        focusAfter(next);
      } else {
        t.category = category; t.needs_review = 0; t.category_source = "manual";
      }
    } catch (err) { toast.error((err as Error).message); }
  }

  let ai = $state<AiSuggest>();
  let aiStatus = $state<"idle" | "asking" | "asked">("idle");
  let aiLog = $state<AiLog>();
</script>

<header class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-1">
    <h1 bind:this={heading} tabindex="-1" class="text-[34px] leading-tight font-bold tracking-tight outline-none">
      Transactions
      <span class="text-base font-normal text-muted-foreground tabular-nums">{list && app.state?.connected ? (review ? (count ? `${count} to go` : "") : String(count)) : ""}</span>
    </h1>
    <AssumptionsLink group="transactions" />
  </div>
  {#if review}
    <Button disabled={!app.state?.has_api_key || aiStatus === "asking"} onclick={() => ai?.run()}
      title={app.state?.has_api_key ? undefined : "Add an OpenRouter key in Settings → Connections first"}>
      {aiStatus === "asking" ? "Asking the AI…" : aiStatus === "asked" ? "Ask again" : "Suggest categories with AI"}
    </Button>
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
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:then [, accounts, recurring]}
  {#if review}
    <AiSuggest bind:this={ai} bind:status={aiStatus} onasked={(failed) => aiLog?.refresh(failed)} onchanged={load} />
    {#if app.state?.has_api_key}<AiLog bind:this={aiLog} />{/if}
  {/if}

  <div class="mb-4 flex flex-wrap items-center gap-2">
    <div class="relative w-full sm:w-72">
      <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input type="search" placeholder="Search" aria-label="Search merchant or description" class="h-10 pl-8"
        value={f.q} oninput={(e) => search(e.currentTarget.value)} />
    </div>
    <NativeSelect aria-label="Account" class="h-10 max-sm:flex-1" bind:value={f.account} onchange={load}>
      <option value="">All accounts</option>
      {#each accounts.filter((a) => a.kind !== "investment") as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </NativeSelect>
    <NativeSelect aria-label="Category" class="h-10 max-sm:flex-1" bind:value={f.category} onchange={load}>
      <option value="">All categories</option>
      <option value="__none__">Uncategorized</option>
      {#each categories.list as c (c.name)}<option value={c.name}>{c.icon ? `${c.icon}  ` : ""}{catLabel(c)}</option>{/each}
    </NativeSelect>
    {#if f.month}
      <span class="inline-flex items-center gap-1 rounded-full bg-primary/15 py-1 pr-1 pl-3 text-sm text-primary">
        {monthLabel(f.month)}{f.scope === "budget" ? " · accounts counted in Budget" : ""}
        <button type="button" class="flex size-5 cursor-pointer items-center justify-center rounded-full hover:bg-primary/20" aria-label="Show all dates"
          onclick={clearMonth}><X class="size-3.5" /></button>
      </span>
    {/if}
    {#if !review}
      {#if ignoredCount > 0 || txShow.ignored}
        <span class="ml-1 text-sm text-muted-foreground max-sm:w-full">{ignoredCount} ignored · <button type="button" class="cursor-pointer font-medium text-primary"
          onclick={() => { txShow.ignored = !txShow.ignored; load(); }}>{txShow.ignored ? "Hide" : "Show"}</button></span>
      {/if}
    {/if}
    {#if isFiltered(f)}
      <Button variant="link" size="sm" class="h-auto px-1 py-0" onclick={clearFilters}>Clear filters</Button>
    {/if}
  </div>

  {#if !review}
    {#await upcoming then events}<Upcoming events={shownEvents(events)} />{/await}
  {/if}

  {#if listError}
    <Card.Root><Card.Content>
      <p class="text-sm">Something went wrong: {listError}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content></Card.Root>
  {:else if !list}
    <div class="h-40 animate-pulse rounded-xl bg-muted" aria-busy="true"></div>
  {:else if !list.items.length}
    {@const sync = syncStatus(app.state).text}
    <Card.Root><Card.Content class="py-6 text-center text-sm text-muted-foreground">
      {#if filtered}
        <p bind:this={caughtUp} tabindex="-1" class="outline-none">No transactions match these filters.</p>
        <Button class="mt-3" variant="outline" onclick={clearFilters}>Clear filters</Button>
      {:else if review}
        <p bind:this={caughtUp} tabindex="-1" class="outline-none">All caught up.</p>
      {:else}
        <p bind:this={caughtUp} tabindex="-1" class="outline-none">No transactions yet. The first sync brings in months of history.</p>
        {#if sync}<p class="mt-1 text-xs">{sync}</p>{/if}
      {/if}
    </Card.Content></Card.Root>
  {:else}
    {#key loads}
      <TxTable items={list.items} total={list.total} {review} {recurring} bind:selecting onsave={save} onchanged={load} onmore={more} />
    {/key}
  {/if}

  {#if app.state?.logodev_configured}
    <p class="mt-3 text-xs text-muted-foreground"><a class="underline underline-offset-4 hover:text-foreground" href="https://logo.dev" target="_blank" rel="noopener">Logos provided by Logo.dev</a></p>
  {/if}
{:catch err}
  <Card.Root><Card.Content><p class="text-sm">Something went wrong: {err.message}</p></Card.Content></Card.Root>
{/await}
{/if}

<RememberBar />
