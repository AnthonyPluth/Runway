<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, route, setQuery } from "$lib/app.svelte";
  import { catLabel, catPath, categories, loadCategories } from "$lib/categories.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import AiLog from "$lib/components/transactions/AiLog.svelte";
  import AiSuggest from "$lib/components/transactions/AiSuggest.svelte";
  import ReviewGroups from "$lib/components/transactions/ReviewGroups.svelte";
  import Switch from "$lib/components/transactions/Switch.svelte";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { reviewAction } from "$lib/components/transactions/reviewKeys";
  import TxTable from "$lib/components/transactions/TxTable.svelte";
  import TxSheet from "$lib/components/transactions/TxSheet.svelte";
  import Plus from "@lucide/svelte/icons/plus";
  import NotConnected from "$lib/components/NotConnected.svelte";
  import Upcoming from "$lib/components/transactions/Upcoming.svelte";
  import { comingUp } from "$lib/components/overview/comingUp";
  import { ruleOffer } from "$lib/components/transactions/remember.svelte";
  import { restoreTx, type Was } from "$lib/components/transactions/restore";
  import type { RecurringItem } from "$lib/components/recurring/types";
  import type { RuleOffer, Tx, TxList, UpcomingEvent } from "$lib/components/transactions/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { amountLabel, clearAll, fromQuery, isFiltered, KINDS, sameFilters, toQuery, txFilters, txShow, type TxFilters } from "$lib/filters.svelte";
  import { fmtSigned, plural } from "$lib/format";
  import DateRange from "$lib/components/transactions/DateRange.svelte";
  import MoreFilters from "$lib/components/transactions/MoreFilters.svelte";
  import { syncStatus } from "$lib/nav.svelte";
  import { undoable } from "$lib/undo";
  import { undoBatched } from "$lib/undoBatch";
  import { cn } from "$lib/utils";
  import { accountName, type Account, type Overview } from "$lib/types";
  import { tick, untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import Search from "@lucide/svelte/icons/search";
  import X from "@lucide/svelte/icons/x";
  import { act, errMsg } from "$lib/act";

  // Transactions (#transactions) and its To review tab (#review) share one list: same filters, same columns.
  // Changing a category saves immediately; in Review the transaction then leaves the list.
  let { page = "transactions", sub: _sub = "" }: { page?: string; sub?: string } = $props();
  // svelte-ignore state_referenced_locally
  const review = page === "review";
  const f: TxFilters = review ? txFilters.review : txFilters.transactions;
  // All's filters are in the address too (#transactions?q=…): opened from one, it has those filters; opened plainly
  // (the sidebar), the ones from last time, written into the address.
  if (!review) {
    if (route.query) Object.assign(f, fromQuery(route.query));
    setQuery(toQuery(f));
  }
  // Back, Forward or a link to other filters while the page is open: load those.
  $effect(() => {
    const q = route.query;   // only the address: what you type changes the filters first, and the address after
    untrack(() => {
      if (review || route.page !== "transactions" || q === toQuery(f)) return;
      const next = fromQuery(q);
      if (sameFilters(next, f)) return;
      clearTimeout(timer); Object.assign(f, next); load();
    });
  });

  // What the page needs before the list: categories, accounts (for the filter) and recurring items (for ↻).
  const loadSetup = () => Promise.all([loadCategories(), api<Account[]>("/api/accounts"), api<RecurringItem[]>("/api/recurring")]);
  let setup = $state(loadSetup());
  // Upcoming (projected) items for the forecast account, and recurring charges on cards, on All only.
  // Loaded again in place after an amount is changed there; the old ones stay on screen until the new ones arrive.
  let upcoming = $state<UpcomingEvent[]>([]);
  function loadUpcoming() {
    if (review) return;
    api<Overview>(`/api/overview?days=${app.state?.horizon_days || 90}`).then((fc) => { upcoming = comingUp(fc, { charges: true }) as UpcomingEvent[]; }, () => {});
  }
  loadUpcoming();

  let list = $state<TxList | null>(null);
  let listError = $state("");        // the list couldn't be loaded at all
  let refreshError = $state("");     // it couldn't be loaded again: the old one stays, marked as not up to date
  let count = $state(0);             // the count in the heading (in Review, goes down as you categorize)
  let loads = $state(0);             // a new search starts the table afresh (no leftover ticks); a reload after a change keeps it
  let applied = $state<TxFilters>({ ...f });   // the filters the list (and Upcoming) was last loaded with
  let appliedIgnored = $state(txShow.ignored);
  // What's marked Ignore under the same search and filters (All only, without a category filter); null while it isn't
  // known (it couldn't be counted), when the line still offers to show them.
  let ignoredCount = $state<number | null>(0);
  let seq = 0;

  async function countIgnored(mine: number, now: TxFilters) {
    if (review) return;
    if (now.category) { ignoredCount = 0; return; }
    const qs = params(now);
    qs.set("ignored", "only"); qs.set("limit", "1"); qs.set("offset", "0");
    try {
      const r = await api<TxList>(`/api/transactions?${qs}`);
      if (mine === seq) ignoredCount = r.total;
    } catch { if (mine === seq) ignoredCount = null; }
  }

  async function load() {
    const mine = ++seq;
    const now = { ...f }, ignored = txShow.ignored;
    if (!review) setQuery(toQuery(now));
    // After a change with the same filters, load as many as were showing, so the list (and where you are in it) stays.
    const same = !!list && ignored === appliedIgnored && (Object.keys(now) as (keyof TxFilters)[]).every((k) => now[k] === applied[k]);
    const qs = query(0, same ? Math.min(1000, Math.max(PAGE, list!.items.length)) : PAGE);
    try {
      const data = await api<TxList>(`/api/transactions?${qs}`);
      if (mine !== seq) return;   // a newer search has been asked for meanwhile
      // The same search again (after a change): the rows are updated where they are, so nothing redraws or jumps. A new
      // search starts the table afresh.
      applied = now; appliedIgnored = ignored; list = data; count = data.total; listError = ""; refreshError = "";
      if (!same) loads++;
      countIgnored(mine, now);
    } catch (err) {
      if (mine !== seq) return;
      // With a list on screen it stays (you may be part way down it), under a line saying it isn't up to date.
      if (list) refreshError = errMsg(err); else listError = errMsg(err);
    }
  }
  const PAGE = 100;
  load();

  // The filters that are set, as the list's query.
  function params(now: TxFilters): URLSearchParams {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(now)) if (v) qs.set(k, v);
    return qs;
  }
  // The list's own conditions besides the filters: Review's queue, or All without what's marked Ignore.
  function scopeOf(): Record<string, string> {
    return review ? { review: "1" } : txShow.ignored ? {} : { ignored: "0" };   // what's marked Ignore stays out of All unless asked for
  }
  function query(offset: number, limit = PAGE) {
    const qs = params(f);
    for (const [k, v] of Object.entries(scopeOf())) qs.set(k, v);
    qs.set("limit", String(limit)); qs.set("offset", String(offset));
    return qs;
  }
  // Every transaction the list shows, for a change to all of them at once ("Select all 212"): its filters as loaded.
  const everyFilter = $derived({ ...Object.fromEntries(params(applied)), ...(review ? { review: "1" } : appliedIgnored ? {} : { ignored: "0" }) });
  // The next page, added to the end (skipping any that shifted in since, e.g. after a sync). A failure is the table's
  // to show (with a Retry).
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
  function setDates(from: string, to: string) { clearTimeout(timer); f.from = from; f.to = to; if (!from && !to) f.scope = ""; load(); }
  function setMore(next: Partial<TxFilters>) { clearTimeout(timer); Object.assign(f, next); load(); }
  function clearFilters() { clearTimeout(timer); clearAll(f); load(); }

  const shownEvents = (events: UpcomingEvent[]) => {
    const a = applied, q = a.q.trim().toLowerCase();
    return events.filter((e) => (!q || e.name.toLowerCase().includes(q)) && (!a.account || e.account_id === a.account) &&
      (!a.category || (a.category === "__none__" ? !e.category : catPath(e.category).includes(a.category))) &&
      (!a.from || e.date >= a.from) && (!a.to || e.date <= a.to) && (!a.min || Math.abs(e.amount) >= Number(a.min) - 0.005) &&
      (!a.max || Math.abs(e.amount) <= Number(a.max) + 0.005) && (a.kind !== "transfer") &&
      (!a.kind || (a.kind === "in" ? e.amount > 0 : e.amount < 0)));
  };

  // After a transaction leaves the list, keyboard focus goes to the next row's category (else the row before it), or,
  // with none left, to "All caught up": without this it falls to the page and you start again from the top. Moving with
  // the keys (j/k), the row itself has the focus (and the ring), so Enter is Review's.
  let heading = $state<HTMLElement>(), caughtUp = $state<HTMLElement>();
  const rowEl = (id: string) => [...document.querySelectorAll<HTMLElement>("[data-tx]")].find((r) => r.dataset.tx === id);
  async function focusAfter(id: string | undefined) {
    await tick();
    if (keyed) keyed = id ?? "";
    const row = id ? rowEl(id) : undefined;
    if (row && keyed) { row.tabIndex = -1; row.focus(); row.scrollIntoView?.({ block: "nearest" }); return; }
    (row?.querySelector<HTMLElement>("[data-category-trigger]") ?? caughtUp ?? heading)?.focus();
  }

  // ------------------------------------------------------------------ Review: the keyboard, Accept, optimistic saves
  // The row the keyboard is on (j/k), by id; "" when none is.
  let keyed = $state("");
  function onkey(e: KeyboardEvent) {
    if (!review || !list?.items.length || grouped) return;
    const action = reviewAction(e, !!keyed && !!list.items.find((x) => x.id === keyed));
    if (!action) return;
    const items = list.items, at = items.findIndex((x) => x.id === keyed), t = at >= 0 ? items[at] : undefined;
    e.preventDefault();
    if (action === "next" || action === "prev") {
      const to = at < 0 ? 0 : Math.max(0, Math.min(items.length - 1, at + (action === "next" ? 1 : -1)));
      keyed = items[to].id; focusAfter(keyed);
    } else if (action === "clear") {
      keyed = ""; (document.activeElement as HTMLElement | null)?.blur?.();
    } else if (t) {
      const row = rowEl(t.id);
      if (action === "enter") { if (t.category && t.needs_review && !t.is_split) accept(t); else row?.querySelector<HTMLElement>("[data-category-trigger]")?.click(); }
      else if (action === "pick") row?.querySelector<HTMLElement>("[data-category-trigger]")?.click();
      else if (action === "split") row?.querySelector<HTMLElement>("[data-split]")?.click();
      else if (action === "ignore" || action === "transfer") {
        const name = action === "ignore" ? "Ignore" : "Transfer";
        if (categories.list.some((c) => c.name === name)) save(t, name); else toast.error(`There’s no ${name} category`);
      }
    }
  }

  /**
   * In Review a transaction leaves the queue as soon as you've decided (and the keyboard moves on); `send` then saves
   * it. If that fails it comes back where it was, and a toast says why. Elsewhere the row stays and updates once saved.
   * `done` gets the reply; null when it failed.
   */
  async function decide<R>(t: Tx, send: () => Promise<R>, done: (r: R) => void | Promise<void>): Promise<boolean> {
    const name = t.payee || t.description || "this transaction";
    if (!(review && list)) {
      return act(async () => { await done(await send()); });
    }
    const at = list.items.findIndex((x) => x.id === t.id);
    const next = at < 0 ? undefined : (list.items[at + 1] ?? list.items[at - 1])?.id;
    if (at >= 0) {
      list.items.splice(at, 1);
      if (count > 0) count--;
      if (list.total > 0) list.total--;
      if (list.items.length) focusAfter(next);
    }
    try {
      await done(await send());
      if (list && !list.items.length) { await load(); await focusAfter(undefined); }
      return true;
    } catch (err) {
      if (list && at >= 0 && !list.items.some((x) => x.id === t.id)) {
        list.items.splice(Math.min(at, list.items.length), 0, t);
        count++; list.total++;
        if (keyed) { keyed = t.id; focusAfter(t.id); }
      }
      toast.error(`Couldn’t save ${name}`, { description: errMsg(err) });
      return false;
    }
  }

  /** Keep the category it has and take it out of Review (whoever set it). */
  function accept(t: Tx): Promise<boolean> {
    const category = t.category ?? "";
    return decide(t, () => api<{ was: Was[] }>(`/api/transactions/${encodeURIComponent(t.id)}/accept`, { method: "POST" }), (r) => {
      undoBatched(`Accepted ${category}`, async () => { await restoreTx(r.was); await load(); }, { description: t.payee || t.description || undefined });
      refreshState();
      if (!review) { t.needs_review = 0; t.category_source = "manual"; }
    });
  }

  // "Accept all ≥ 90%": the loaded rows waiting with a category it's sure of (all of them with a category, when there
  // are no confidences to go by). More than CONFIRM_AT asks first.
  const CONFIRM_AT = 10;
  const confident = $derived((list?.items ?? []).filter((t) => t.needs_review && t.category && !t.is_split && (t.confidence ?? 0) >= 0.9));
  const sure = $derived((list?.items ?? []).some((t) => t.needs_review && t.category && t.confidence != null));
  const acceptable = $derived(sure ? confident : (list?.items ?? []).filter((t) => t.needs_review && t.category && !t.is_split));
  let askAll = $state(false);
  async function acceptAll() {
    if (!list) return false;
    const rows = [...acceptable], ids = new Set(rows.map((t) => t.id));
    const before = list.items.map((t) => t.id);
    list.items = list.items.filter((t) => !ids.has(t.id));
    count = Math.max(0, count - rows.length); list.total = Math.max(0, list.total - rows.length);
    try {
      const r = await api<{ updated: number; was: Was[] }>("/api/transactions/bulk", { method: "POST", body: { ids: [...ids], reviewed: true } });
      undoable(`Accepted ${plural(r.updated, "transaction")}`, async () => { await restoreTx(r.was); await load(); });
      refreshState();
      if (!list.items.length) await load();
      return true;
    } catch (err) {
      // Back where they were.
      const byId = new Map([...rows, ...list.items].map((t) => [t.id, t]));
      list.items = before.map((id) => byId.get(id)).filter((t): t is Tx => !!t);
      count += rows.length; list.total += rows.length;
      toast.error(errMsg(err));
      return false;
    }
  }
  function startAcceptAll() { if (acceptable.length > CONFIRM_AT) askAll = true; else acceptAll(); }

  // Review a merchant at a time (without an AI key, which does that with suggestions). Off unless you turn it on.
  let grouped = $state(false);
  function groupApplied(ids: string[]) {
    if (!list) return;
    const gone = new Set(ids);
    const was = list.items.length;
    list.items = list.items.filter((t) => !gone.has(t.id));
    const n = was - list.items.length;
    count = Math.max(0, count - n); list.total = Math.max(0, list.total - n);
    if (!list.items.length) load();
  }

  // The category filter the list was loaded with, when it's a category: a split transaction's part in it is what shows.
  const only = $derived(applied.category && applied.category !== "__none__" ? applied.category : "");

  // Saves a category picked for a transaction; false when it couldn't be saved (the picker then shows the saved one again).
  // The toast says what it was, so a wrong pick (or a slip of the keyboard) can be taken back; changes made one after
  // another share it ("3 changed · Undo"). Its main button offers to use the category for the merchant from now on.
  async function save(t: Tx, category: string): Promise<boolean> {
    if (t.match && only) return savePart(t, category);
    const prev = t.category ?? "";
    return decide(t, () => api<{ also_updated: number; offer_rule: RuleOffer | null; was: Was[] }>(
      `/api/transactions/${encodeURIComponent(t.id)}/category`, { method: "POST", body: { category } }), async (r) => {
      const offer = r.offer_rule ? ruleOffer(t.id, category, r.offer_rule, load) : null;
      undoBatched(offer ? category : prev === category ? `Accepted ${category}` : `${prev || "Uncategorized"} → ${category}`,
        async () => { await restoreTx(r.was); await load(); },
        { description: offer?.description || t.payee || t.description || undefined, also: offer?.also });
      refreshState();
      if (r.also_updated) { await load(); return; }
      if (!review) {
        t.category = category; t.needs_review = 0; t.category_source = "manual";
        load();   // the sum above the list (and a filter it may have left) follow
      }
    });
  }

  // Only the part shown changes (its order's items, when an order split it); the whole transaction takes the category
  // once every part has it. The list loads again: the part may have left the filter.
  async function savePart(t: Tx, category: string) {
    return act(async () => {
      const r = await api<{ was: Was[] }>(`/api/transactions/${encodeURIComponent(t.id)}/category`, { method: "POST", body: { category, only } });
      undoable(`${t.match!.categories.join(", ")} → ${category}`, async () => { await restoreTx(r.was); await load(); },
        { description: t.payee || t.description || undefined });
      refreshState();
      await load();
    });
  }

  // The sheet: a transaction's details (by id, so it shows the list's latest copy after a reload; the one it was opened
  // with if it has left the list, as in Review), or a new one to add.
  let sheetOpen = $state(false);
  let sheetTx = $state<Tx | null>(null);
  const shown = $derived(sheetTx ? (list?.items.find((x) => x.id === sheetTx!.id) ?? sheetTx) : null);
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
    <h1 bind:this={heading} tabindex="-1" class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em] outline-none">Transactions</h1>
    {#if review}<span class="text-base font-normal text-muted-foreground tabular-nums">{list && app.state?.connected && count ? `${count} to go` : ""}</span>{/if}
  </div>
  {#if !review && app.state?.connected}
    <Button variant="outline" onclick={openAdd} title="Add a transaction by hand"><Plus />Add</Button>
  {/if}
  {#if review && app.state?.has_api_key}
    <Button disabled={aiStatus === "asking"} onclick={() => ai?.run()}>
      {aiStatus === "asking" ? "Asking the AI…" : aiStatus === "asked" ? "Ask again" : "Suggest categories with AI"}
    </Button>
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
    <AiSuggest bind:this={ai} bind:status={aiStatus} onasked={(failed) => aiLog?.refresh(failed)} onchanged={load} />
    {#if app.state?.has_api_key}<AiLog bind:this={aiLog} />{/if}
  {/if}

  <div class="mb-3 flex flex-wrap items-center gap-2">
    <div class="relative w-full sm:w-64">
      <Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input type="search" placeholder="Search" aria-label="Search transactions" title="Merchant, bank’s text, category, note or amount" class="h-10 pl-8"
        value={f.q} oninput={(e) => search(e.currentTarget.value)} />
    </div>
    <NativeSelect aria-label="Account" class="h-10 max-sm:min-w-0 max-sm:flex-[1_1_40%] sm:max-w-48" bind:value={f.account} onchange={load}>
      <option value="">All accounts</option>
      {#each accounts.filter((a) => a.kind !== "investment") as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </NativeSelect>
    <NativeSelect aria-label="Category" class="h-10 max-sm:min-w-0 max-sm:flex-[1_1_40%] sm:max-w-52" bind:value={f.category} onchange={load}>
      <option value="">All categories</option>
      <option value="__none__">Uncategorized</option>
      {#each categories.list as c (c.name)}<option value={c.name}>{c.icon ? `${c.icon}  ` : ""}{catLabel(c)}</option>{/each}
    </NativeSelect>
    <DateRange from={f.from} to={f.to} note={f.scope === "budget" ? "Budget accounts" : ""} onchange={setDates} />
    <MoreFilters kind={f.kind} min={f.min} max={f.max} onchange={setMore} />
    {#if f.kind}{@render chip(KINDS.find((k) => k.value === f.kind)?.label ?? f.kind, "Show every kind", () => setMore({ kind: "" }))}{/if}
    {#if f.min || f.max}{@render chip(amountLabel(f.min, f.max), "Any amount", () => setMore({ min: "", max: "" }))}{/if}
    {#if isFiltered(f)}
      <Button variant="link" size="sm" class="h-auto px-1 py-0" onclick={clearFilters}>Clear filters</Button>
    {/if}
  </div>

  {#if review && list?.items.length}
    <!-- Review's own line: accept what's certain at once, review a merchant at a time, and (desktop) the keys. -->
    <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
      {#if acceptable.length && !grouped}
        <Button variant="outline" size="sm" onclick={startAcceptAll}
          title={sure ? "Keep the categories it’s at least 90% sure of" : "Keep every category suggested so far"}>
          {sure ? "Accept all ≥ 90%" : "Accept all suggestions"} <span class="tabular-nums text-muted-foreground">{acceptable.length}</span></Button>
      {/if}
      {#if !app.state?.has_api_key}
        <div class="w-48"><Switch checked={grouped} label="Group by merchant" onchange={(on) => { grouped = on; keyed = ""; }} /></div>
      {/if}
      {#if !grouped}
        <p class="ml-auto text-xs text-muted-foreground max-md:hidden [@media(hover:none)]:hidden [&_kbd]:rounded [&_kbd]:border [&_kbd]:px-1 [&_kbd]:font-sans" data-keys-hint>
          <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>Enter</kbd> accept · <kbd>c</kbd> category</p>
      {/if}
    </div>
  {/if}

  {#if !review && list}
    <!-- How many, and what they add up to (as the day totals count: transfers aren't money in or out). Not up to date
         after a failed reload, so it's dimmed then. -->
    <p class={cn("mb-4 text-sm text-muted-foreground tabular-nums", refreshError && "opacity-50")} data-summary>
      {plural(list.total, "transaction")} · <span title={applied.kind === "transfer" ? undefined : "Transfers not counted"}>{fmtSigned(list.sum ?? 0)} net</span>
      {#if ignoredCount !== 0 || txShow.ignored}
        · {ignoredCount == null ? "Ignored" : `${ignoredCount} ignored`} <button type="button" class="cursor-pointer font-medium text-primary"
          aria-expanded={txShow.ignored} aria-label={`${txShow.ignored ? "Hide" : "Show"} ignored transactions`}
          onclick={() => { txShow.ignored = !txShow.ignored; load(); }}>{txShow.ignored ? "Hide" : "Show"}</button>
      {/if}
    </p>
  {/if}

  {#if !review}
    <Upcoming events={shownEvents(upcoming)} oneAccount={!!applied.account} onchanged={loadUpcoming} />
  {/if}

  {#if refreshError && list}
    <div role="alert" class="mb-3 flex flex-wrap items-center gap-x-2 rounded-lg bg-warning/10 px-3 py-2 text-sm">
      <span>Couldn’t refresh</span><span class="text-muted-foreground">· {refreshError}</span>
      <Button variant="link" size="sm" class="ml-auto h-auto p-0" onclick={load}>Retry</Button>
    </div>
  {/if}
  {#if listError && !list}
    <Card.Root><Card.Content>
      <p class="text-sm">Something went wrong: {listError}</p>
      <Button class="mt-3" variant="outline" onclick={load}>Try again</Button>
    </Card.Content></Card.Root>
  {:else if !list}
    <div class="h-40 animate-pulse motion-reduce:animate-none rounded-xl bg-muted" role="status" aria-busy="true"><span class="sr-only">Loading…</span></div>
  {:else if !list.items.length}
    {@const sync = syncStatus(app.state).text}
    <Card.Root><Card.Content class="py-6 text-center text-sm text-muted-foreground">
      {#if filtered}
        <p bind:this={caughtUp} tabindex="-1" class="outline-none">No transactions match these filters.</p>
        <Button class="mt-3" variant="outline" onclick={clearFilters}>Clear filters</Button>
      {:else if review}
        <p bind:this={caughtUp} tabindex="-1" class="outline-none">All caught up.</p>
        <p class="mt-1"><a href="#transactions" class="font-medium text-primary">See all transactions</a> or check back after the next sync.</p>
      {:else}
        <p bind:this={caughtUp} tabindex="-1" class="outline-none">No transactions yet. The first sync brings in months of history.</p>
        {#if sync}<p class="mt-1 text-xs">{sync}</p>{/if}
      {/if}
    </Card.Content></Card.Root>
  {:else}
    {#if review && grouped}
      <ReviewGroups items={list.items} onapplied={groupApplied} onchanged={load} />
    {:else}
      {#key loads}
        <TxTable items={list.items} total={list.total} {review} {recurring} {only} family={list.family} oneAccount={!!applied.account} every={everyFilter}
          bind:selecting focused={keyed} onsave={save} onaccept={accept} onchanged={load} onmore={more} onopen={openTx} />
      {/key}
    {/if}
  {/if}

  <TxSheet bind:open={sheetOpen} t={shown} {accounts} account={applied.account} {recurring} family={list?.family} onsave={saveFromSheet} onchanged={load}
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

<svelte:window onkeydown={onkey} />

{#if askAll}
  <ConfirmDialog bind:open={askAll} title={`Accept ${plural(acceptable.length, "transaction")}?`}
    description="They keep their categories and leave To review. You can undo it afterwards." confirmLabel="Accept" busyLabel="Accepting…"
    onconfirm={acceptAll} />
{/if}

{#snippet chip(label: string, clear: string, onclear: () => void)}
  <span class="inline-flex h-8 items-center gap-1 rounded-full bg-primary/15 pr-1 pl-3 text-sm text-primary">
    {label}
    <button type="button" class="flex size-6 cursor-pointer items-center justify-center rounded-full hover:bg-primary/20" aria-label={clear} title={clear}
      onclick={onclear}><X class="size-3.5" /></button>
  </span>
{/snippet}
