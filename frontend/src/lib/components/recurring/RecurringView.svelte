<script lang="ts" module>
  import { actGet, act } from "$lib/act";
  // Which items you've opened: they stay open when the page loads again (after a save, a sync), like the classic app.
  const openRecurring = new Set<string>();
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import MissedAlert from "$lib/components/MissedAlert.svelte";
  import RecIcon from "$lib/components/recurring/RecIcon.svelte";
  import RecurringFields from "$lib/components/recurring/RecurringFields.svelte";
  import RecurringItem from "$lib/components/recurring/RecurringItem.svelte";
  import { byDue, monthlyTotal } from "$lib/components/recurring/schedule";
  import { FREQ, validate, type RecurringItem as Item, type RecurringValues, type DismissedSuggestion, type Suggestion } from "$lib/components/recurring/types";
  import { linkCls } from "$lib/components/settings/ui";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { fmt0, fmtDate, fmtSigned, isoDay, nb } from "$lib/format";
  import type { Account, Missed } from "$lib/types";
  import { undoable } from "$lib/undo";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { tick } from "svelte";

  // The Recurring page: its heading and Add, missed payments (Needs attention), the add form, the items in Money in and
  // Money out (each soonest due first, with what they come to a month), and what's spotted in your history. Without a
  // bank, the items you add by hand still work; a line says what a bank adds.

  // #recurring?item=7 (an upcoming item's name links here): that item starts open and comes into view.
  const asked = new URLSearchParams(location.hash.split("?")[1] ?? "").get("item");
  if (asked) openRecurring.add(asked);

  const connected = $derived(!!app.state?.connected);
  const today = isoDay();
  let accounts = $state<Account[]>([]);
  let items = $state<Item[]>([]);
  let suggestions = $state<Suggestion[]>([]);
  let dismissed = $state<DismissedSuggestion[]>([]);
  let loaded = $state(false);    // the first load came in
  let failed = $state(false);    // the latest load didn't: the page says so (no stale list) and offers Retry
  let loading = $state(false);

  // Loads (or loads again) this page's data. A failure shows the error in place of the list, never the old list.
  async function load() {
    loading = true;
    try {
      const [a, list] = await Promise.all([api<Account[]>("/api/accounts"), api<Item[]>("/api/recurring")]);
      accounts = a; items = list; failed = false;
      if (!loaded) {
        // The Add form starts open when there's nothing yet, with your primary account chosen.
        adding = !list.length;
        primary = app.state?.primary_account || a.find((x) => !x.hidden)?.id || "";
        blank.account_id = primary;
      }
      loaded = true;
    } catch { failed = true; }
    finally { loading = false; }
    if (!failed) await loadSuggestions();
  }
  // What's spotted in your history is extra: if it can't be looked up, the page goes on without it.
  async function loadSuggestions() {
    if (!connected) { suggestions = []; dismissed = []; return; }
    try {
      [suggestions, dismissed] = await Promise.all([api<Suggestion[]>("/api/recurring/suggestions"), api<DismissedSuggestion[]>("/api/recurring/suggestions/dismissed")]);
    } catch { suggestions = []; dismissed = []; }
    if (!dismissed.length) showDismissed = false;
  }
  // A new item starts today, monthly, for the primary account.
  const fresh = (): RecurringValues => ({ name: "", account_id: primary, amount: null, amount_mode: "fixed", frequency: "monthly", dates: "", anchor_date: isoDay(), match: "", amount_min: "", amount_max: "", end_date: "" });
  let primary = "";
  let adding = $state(false);
  let blank: RecurringValues = $state(fresh());
  let showDismissed = $state(false);
  let showSpotted = $state(false);
  let busy = $state(false);
  let submitted = $state(false);   // after the first Add, the fields that need fixing say so
  let formKey = $state(0);         // a new key starts the fields over (they read the amount once)
  const errors = $derived(submitted ? validate(blank) : {});
  let form = $state<HTMLElement | null>(null);
  load();

  // Money in and money out, by what the forecast expects (the usual amount, or the fixed one); soonest due first.
  const moneyIn = $derived(items.filter((r) => (r.expected_amount ?? r.amount) > 0).sort(byDue));
  const moneyOut = $derived(items.filter((r) => !((r.expected_amount ?? r.amount) > 0)).sort(byDue));

  // Missed payments, newest first, until you link or skip them (Undo puts one back).
  let cleared = $state<string[]>([]);
  const attention = $derived(items.flatMap((r) => (r.missed ?? []).filter((m) => !cleared.includes(m.key))
    .map((m): Missed => ({ ...m, recurring_id: r.id, account_id: r.account_id, account_name: r.account_name ?? undefined })))
    .sort((a, b) => b.date.localeCompare(a.date)));
  const missedFor = (r: Item) => (r.missed ?? []).filter((m) => !cleared.includes(m.key)).length;

  async function focusForm() { await tick(); form?.querySelector<HTMLInputElement>("input[name=name]")?.focus(); }
  async function openForm() { if (!adding) startOver(); adding = true; await focusForm(); }
  function startOver() { Object.assign(blank, fresh()); submitted = false; formKey++; }
  function cancel() { adding = false; startOver(); }
  async function add() {
    if (busy) return;
    submitted = true;
    if (Object.keys(validate(blank)).length) { await tick(); form?.querySelector<HTMLElement>("[aria-invalid=true]")?.focus(); return; }
    await act(async () => {
      const r = await api<{ linked: number }>("/api/recurring", { method: "POST", body: { ...blank, active: 1 } });
      toast.success(r.linked ? `Added · matched ${r.linked} past transactions` : "Added");
      adding = false; startOver();
      await load();
    }, { busy: (on) => (busy = on) });
  }
  // "Add" on a suggestion adds it as it is (Undo removes it); "Edit first" fills the form with it to adjust.
  const values = (s: Suggestion): RecurringValues => ({ name: s.name, account_id: s.account_id, amount: s.amount, amount_mode: "fixed", frequency: s.frequency, dates: "", anchor_date: s.anchor_date, match: s.match, amount_min: "", amount_max: "", end_date: "" });
  async function addSuggestion(s: Suggestion) {
    const r = await actGet(() => api<{ id: number; linked: number }>("/api/recurring", { method: "POST", body: { ...values(s), active: 1 } }));
    if (!r) return;
    await load();
    undoable(r.linked ? `Added ${s.name} · matched ${r.linked}` : `Added ${s.name}`, async () => {
      await api(`/api/recurring/${r.id}`, { method: "DELETE" });
      await load();
    });
  }
  async function editSuggestion(s: Suggestion) {
    Object.assign(blank, values(s));
    submitted = false; formKey++; adding = true;
    await focusForm();
  }
  async function dismissSuggestion(s: Suggestion) {
    await act(async () => {
      await api("/api/recurring/suggestions/dismiss", { method: "POST", body: { key: s.key } });
      suggestions = suggestions.filter((x) => x.key !== s.key);
      dismissed = [...dismissed, { key: s.key, account_id: s.account_id, match: s.match, name: s.name, frequency: s.frequency }];
      toast(`Okay, ${s.name} won’t be suggested again`);
    });
  }
  // Putting one back makes it a suggestion again (if it still looks recurring), so look the lists up again.
  async function restoreSuggestion(d: DismissedSuggestion) {
    await act(async () => {
      await api("/api/recurring/suggestions/restore", { method: "POST", body: { key: d.key } });
      await loadSuggestions();
      toast.success(`${d.name ?? d.match} can be suggested again`);
    });
  }
  function toggle(id: number, open: boolean) { if (open) openRecurring.add(String(id)); else openRecurring.delete(String(id)); }

  // "−$12–$15" when the last few came to different amounts, else the usual one.
  function suggestionAmount(s: Suggestion): string {
    const [lo, hi] = [s.amount_low, s.amount_high];
    return lo != null && hi != null && fmt0(lo) !== fmt0(hi) ? `${s.amount > 0 ? "+" : "−"}${fmt0(lo)}–${fmt0(hi)}` : fmtSigned(s.amount);
  }
  const perMonth = (list: Item[], way: string) => { const n = monthlyTotal(list); return n >= 0.5 ? `≈ ${fmt0(n)} a month ${way}` : undefined; };
</script>

{#snippet group(title: string, list: Item[], way: string)}
  {#if list.length}
    <div role="region" aria-label={title}>
      <Group {title} inset="3.75rem" class="mb-6" footer={perMonth(list, way)}>
        {#each list as r (r.id)}
          <RecurringItem {r} {accounts} {today} missed={missedFor(r)} open={openRecurring.has(String(r.id))} focus={asked === String(r.id)}
            ontoggle={(o) => toggle(r.id, o)} onsaved={(list) => (items = list)} />
        {/each}
      </Group>
    </div>
  {/if}
{/snippet}

{#snippet dismissedList()}
  {#if dismissed.length}
    <section class="px-4 text-sm text-muted-foreground" aria-label="Dismissed suggestions">
      <p>{dismissed.length} dismissed ·
        <button type="button" class={linkCls} aria-expanded={showDismissed} onclick={() => (showDismissed = !showDismissed)}>{showDismissed ? "Hide" : "Show"}</button></p>
      {#if showDismissed}
        <ul class="mt-2 flex flex-col gap-1.5">
          {#each dismissed as d (d.key)}
            <li class="flex items-center gap-3">
              <span class="flex min-w-0 flex-1 flex-col">
                <span class="truncate text-foreground">{d.name ?? d.match}</span>
                <span class="text-xs">{nb(FREQ[d.frequency] ?? d.frequency)}{d.account_name ? ` · ${d.account_name}` : ""}</span>
              </span>
              <Button variant="outline" size="sm" aria-label={`Restore ${d.name ?? d.match}`} onclick={() => restoreSuggestion(d)}>Restore</Button>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
{/snippet}

{#snippet spotted()}
  <Group title="Spotted in your history" inset="3.75rem" class="mb-3">
    {#each suggestions as s (s.key)}
      <div class="cell flex-wrap" data-suggestion={s.key}>
        <RecIcon id={s.account_id} />
        <span class="flex min-w-0 flex-1 flex-col gap-0.5">
          <span class="truncate text-[15px] font-medium">{s.name}</span>
          <span class="text-[13px] text-muted-foreground">{nb(FREQ[s.frequency] ?? s.frequency)} · {nb(`${s.count}×`)} · {nb(`last ${fmtDate(s.anchor_date)}`)}</span>
        </span>
        <span class={cn("shrink-0 text-[15px] font-medium tabular-nums", s.amount > 0 && "text-good")}>{suggestionAmount(s)}</span>
        <span class="flex shrink-0 gap-1 max-lg:basis-full max-lg:pl-10">
          <Button variant="outline" size="sm" aria-label={`Add ${s.name}`} onclick={() => addSuggestion(s)}>Add</Button>
          <Button variant="ghost" size="sm" aria-label={`Edit ${s.name} first`} onclick={() => editSuggestion(s)}>Edit first</Button>
          <Button variant="ghost" size="sm" class="text-muted-foreground" aria-label={`${s.name} is not recurring`} onclick={() => dismissSuggestion(s)}>Not recurring</Button>
        </span>
      </div>
    {/each}
  </Group>
{/snippet}

<div class="mb-6 flex items-center justify-between gap-3">
  <h1 class="text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Recurring</h1>
  {#if loaded && !failed}
    <Button variant="outline" size="sm" title="You can also start one from any transaction, with its repeat button" onclick={openForm}>Add</Button>
  {/if}
</div>
{#if !connected}
  <p class="-mt-3 mb-6 text-sm text-muted-foreground">No bank connected, so payments aren’t matched or spotted. <a class="font-medium text-primary" href="#setup/connections">Connect a bank</a></p>
{/if}

{#if failed}
  <div class="tile flex flex-wrap items-center gap-3" role="alert">
    <p class="min-w-0 flex-1 text-sm">Couldn’t load your recurring items.</p>
    <Button variant="outline" size="sm" disabled={loading} onclick={load}>{loading ? "Retrying…" : "Retry"}</Button>
  </div>
{:else if !loaded}
  <!-- Shaped like the list it stands in for: a heading, then rows of a logo, two lines and an amount. -->
  <div class="animate-pulse motion-reduce:animate-none" role="status" aria-label="Loading recurring items">
    <div class="mb-2.5 ml-4 h-3 w-24 rounded bg-muted"></div>
    <div class="group-list" style:--inset="3.75rem">
      {#each [0, 1, 2, 3] as i (i)}
        <div class="cell">
          <div class="size-7 shrink-0 rounded-md bg-muted"></div>
          <div class="flex flex-1 flex-col gap-1.5"><div class="h-3.5 w-36 max-w-full rounded bg-muted"></div><div class="h-3 w-52 max-w-full rounded bg-muted/60"></div></div>
          <div class="h-3.5 w-16 shrink-0 rounded bg-muted"></div>
        </div>
      {/each}
    </div>
  </div>
{:else}
  {#if attention.length}
    <Group title="Needs attention" inset="3.75rem" class="mb-6">
      {#each attention as m (m.key)}
        <MissedAlert {m} {today} ondone={(k) => (cleared = [...cleared, k])} onundone={(k) => (cleared = cleared.filter((x) => x !== k))} />
      {/each}
    </Group>
  {/if}

  <!-- With nothing yet, what's spotted in your history comes first: it's the quickest way to start. -->
  {#if !items.length && suggestions.length}<div class="mb-6">{@render spotted()}{@render dismissedList()}</div>{/if}

  {#if adding}
    <Card.Root class="mb-6">
      <Card.Content>
        <section bind:this={form} aria-labelledby="add-recurring">
          <h2 id="add-recurring" class="mb-3 text-sm font-medium">Add a recurring item</h2>
          <form novalidate onsubmit={(e) => { e.preventDefault(); add(); }}>
            {#key formKey}<RecurringFields bind:v={blank} {accounts} {errors} />{/key}
            <div class="mt-4 flex items-center gap-2">
              <Button type="submit" disabled={busy}>{busy ? "Adding…" : "Add"}</Button>
              {#if items.length}<Button type="button" variant="link" onclick={cancel}>Cancel</Button>{/if}
            </div>
          </form>
        </section>
      </Card.Content>
    </Card.Root>
  {/if}

  {@render group("Money in", moneyIn, "in")}
  {@render group("Money out", moneyOut, "out")}

  {#if items.length && suggestions.length}
    {#if showSpotted}
      <div class="mb-6">{@render spotted()}
        <p class="mb-3 px-4 text-sm text-muted-foreground"><button type="button" class={linkCls} aria-expanded="true" onclick={() => (showSpotted = false)}>Hide suggestions</button></p>
        {@render dismissedList()}</div>
    {:else}
      <p class="mb-6 px-4 text-sm text-muted-foreground">{suggestions.length} spotted in your history ·
        <button type="button" class={linkCls} aria-expanded="false" onclick={() => (showSpotted = true)}>Show</button></p>
    {/if}
  {:else if !suggestions.length && dismissed.length}
    <div class="mb-6">{@render dismissedList()}</div>
  {/if}
{/if}
