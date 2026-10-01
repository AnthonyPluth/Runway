<script lang="ts" module>
  // Which items you've opened: they stay open when the page loads again (after a save, a sync), like the classic app.
  const openRecurring = new Set<string>();
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import RecIcon from "$lib/components/recurring/RecIcon.svelte";
  import RecurringFields from "$lib/components/recurring/RecurringFields.svelte";
  import RecurringItem from "$lib/components/recurring/RecurringItem.svelte";
  import { FREQ, validate, type RecurringItem as Item, type RecurringValues, type DismissedSuggestion, type Suggestion } from "$lib/components/recurring/types";
  import { linkCls } from "$lib/components/settings/ui";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, fmtDate, isoDay, nb } from "$lib/format";
  import type { Account } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { tick } from "svelte";
  import Plus from "@lucide/svelte/icons/plus";

  type Data = { accounts: Account[] };
  async function load(): Promise<Data> {
    const [accounts, list] = await Promise.all([api<Account[]>("/api/accounts"), api<Item[]>("/api/recurring")]);
    items = list;
    if (app.state?.connected) await loadSuggestions(); else { suggestions = []; dismissed = []; }
    // The Add form starts open when there's nothing yet, with your primary account chosen.
    adding = !list.length;
    primary = app.state?.primary_account || accounts.find((a) => !a.hidden)?.id || "";
    blank.account_id = primary;
    return { accounts };
  }
  async function loadSuggestions() {
    [suggestions, dismissed] = await Promise.all([api<Suggestion[]>("/api/recurring/suggestions"), api<DismissedSuggestion[]>("/api/recurring/suggestions/dismissed")]);
    if (!dismissed.length) showDismissed = false;
  }
  // A new item starts today, monthly, for the primary account.
  const fresh = (): RecurringValues => ({ name: "", account_id: primary, amount: null, amount_mode: "fixed", frequency: "monthly", dates: "", anchor_date: isoDay(), match: "", amount_min: "", amount_max: "" });
  let primary = "";
  let adding = $state(false);
  let blank: RecurringValues = $state(fresh());
  let items = $state<Item[]>([]);
  let suggestions = $state<Suggestion[]>([]);
  let dismissed = $state<DismissedSuggestion[]>([]);
  let showDismissed = $state(false);
  let busy = $state(false);
  let submitted = $state(false);   // after the first Add, the fields that need fixing say so
  let formKey = $state(0);         // a new key starts the fields over (they read the amount once)
  const errors = $derived(submitted ? validate(blank) : {});
  const data = load();
  let form = $state<HTMLElement | null>(null);

  // Money in and money out, by what the forecast expects (the usual amount, or the fixed one).
  const moneyIn = (items: Item[]) => items.filter((r) => (r.expected_amount ?? r.amount) > 0);
  const moneyOut = (items: Item[]) => items.filter((r) => !((r.expected_amount ?? r.amount) > 0));

  async function focusForm() { await tick(); form?.querySelector<HTMLInputElement>("input[name=name]")?.focus(); }
  async function openForm() { if (!adding) startOver(); adding = true; await focusForm(); }
  function startOver() { Object.assign(blank, fresh()); submitted = false; formKey++; }
  function cancel() { adding = false; startOver(); }
  async function add() {
    if (busy) return;
    submitted = true;
    if (Object.keys(validate(blank)).length) { await tick(); form?.querySelector<HTMLElement>("[aria-invalid=true]")?.focus(); return; }
    busy = true;
    try {
      const r = await api<{ linked: number }>("/api/recurring", { method: "POST", body: { ...blank, active: 1 } });
      toast.success(r.linked ? `Added · matched ${r.linked} past transactions` : "Added"); reload();
    } catch (err) { toast.error((err as Error).message); }
    finally { busy = false; }
  }
  // "Add" on a suggestion fills the form with it, so you can adjust the name, amount or schedule first.
  async function useSuggestion(s: Suggestion) {
    Object.assign(blank, { name: s.name, account_id: s.account_id, amount: s.amount, amount_mode: "fixed", frequency: s.frequency, dates: "", anchor_date: s.anchor_date, match: s.match, amount_min: "", amount_max: "" });
    submitted = false; formKey++; adding = true;
    await focusForm();
  }
  async function dismissSuggestion(s: Suggestion) {
    try {
      await api("/api/recurring/suggestions/dismiss", { method: "POST", body: { key: s.key } });
      suggestions = suggestions.filter((x) => x.key !== s.key);
      dismissed = [...dismissed, { key: s.key, account_id: s.account_id, match: s.match, frequency: s.frequency }];
      toast(`Okay, ${s.name} won’t be suggested again`);
    } catch (err) { toast.error((err as Error).message); }
  }
  // Putting one back makes it a suggestion again (if it still looks recurring), so look the lists up again.
  async function restoreSuggestion(d: DismissedSuggestion) {
    try {
      await api("/api/recurring/suggestions/restore", { method: "POST", body: { key: d.key } });
      await loadSuggestions();
      toast.success(`${d.match} can be suggested again`);
    } catch (err) { toast.error((err as Error).message); }
  }
  function toggle(id: number, open: boolean) { if (open) openRecurring.add(String(id)); else openRecurring.delete(String(id)); }
</script>

<div class="mb-6 flex items-center justify-end gap-4">
  <Button onclick={openForm}><Plus />Add</Button>
</div>

{#snippet group(title: string, list: Item[], accounts: Account[])}
  {#if list.length}
    <section aria-label={title}>
      <h2 class="mb-1 text-xs font-medium tracking-wider text-muted-foreground uppercase">{title}</h2>
      <div>
        {#each list as r (r.id)}
          <RecurringItem {r} {accounts} open={openRecurring.has(String(r.id))} ontoggle={(o) => toggle(r.id, o)} onsaved={(list) => (items = list)} />
        {/each}
      </div>
    </section>
  {/if}
{/snippet}

{#snippet dismissedList()}
  {#if dismissed.length}
    <section class="text-sm text-muted-foreground" aria-label="Dismissed suggestions">
      <p>{dismissed.length} dismissed ·
        <button type="button" class={linkCls} aria-expanded={showDismissed} onclick={() => (showDismissed = !showDismissed)}>{showDismissed ? "Hide" : "Show"}</button></p>
      {#if showDismissed}
        <ul class="mt-2 flex flex-col gap-1.5">
          {#each dismissed as d (d.key)}
            <li class="flex items-center gap-3">
              <span class="flex min-w-0 flex-1 flex-col">
                <span class="truncate text-foreground">{d.match}</span>
                <span class="text-xs">{nb(FREQ[d.frequency] ?? d.frequency)}{d.account_name ? ` · ${d.account_name}` : ""}</span>
              </span>
              <Button variant="outline" size="sm" aria-label={`Restore ${d.match}`} onclick={() => restoreSuggestion(d)}>Restore</Button>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
{/snippet}

{#snippet suggestionsCard()}
  {#if !suggestions.length && dismissed.length}
    <div class="mb-6">{@render dismissedList()}</div>
  {:else if suggestions.length}
    <Card.Root class="mb-6">
      <Card.Header>
        <Card.Title><h2>Spotted in your history</h2></Card.Title>
      </Card.Header>
      <Card.Content class="flex flex-col">
        {#each suggestions as s (s.key)}
          <div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-t py-3 first:border-t-0 first:pt-0">
            <RecIcon id={s.account_id} />
            <span class="flex min-w-40 flex-1 flex-col gap-0.5">
              <span class="truncate font-medium">{s.name}</span>
              <span class="text-xs text-muted-foreground">{nb(s.frequency)} · {nb(`${s.count}×`)} · {nb(`last ${fmtDate(s.anchor_date)}`)}</span>
            </span>
            <span class={cn("shrink-0 font-medium tabular-nums", s.amount > 0 && "text-emerald-500")}>{fmt(s.amount)}</span>
            <span class="flex shrink-0 gap-1">
              <Button variant="outline" size="sm" aria-label={`Add ${s.name}`} onclick={() => useSuggestion(s)}>Add</Button>
              <Button variant="ghost" size="sm" aria-label={`${s.name} is not recurring`} onclick={() => dismissSuggestion(s)}>Not recurring</Button>
            </span>
          </div>
        {/each}
        {#if dismissed.length}<div class="border-t pt-3">{@render dismissedList()}</div>{/if}
      </Card.Content>
    </Card.Root>
  {/if}
{/snippet}

{#await data}
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:then d}
  {#if adding}
    <Card.Root class="mb-6" bind:ref={form}>
      <Card.Header>
        <Card.Title><h2>Add a recurring item</h2></Card.Title>
        <Card.Description>Fields marked * are required. Pick money out for a bill, money in for a paycheck.</Card.Description>
      </Card.Header>
      <Card.Content>
        <form novalidate onsubmit={(e) => { e.preventDefault(); add(); }}>
          {#key formKey}<RecurringFields bind:v={blank} accounts={d.accounts} {errors} />{/key}
          <div class="mt-4 flex items-center gap-2">
            <Button type="submit" disabled={busy}>{busy ? "Adding…" : "Add"}</Button>
            {#if items.length}<Button type="button" variant="link" onclick={cancel}>Cancel</Button>{/if}
          </div>
        </form>
      </Card.Content>
    </Card.Root>
  {/if}

  {#if !items.length}{@render suggestionsCard()}{/if}

  {#if items.length}
    <Card.Root class="mb-6">
      <Card.Content class="flex flex-col gap-6">
        {@render group("Money in", moneyIn(items), d.accounts)}
        {@render group("Money out", moneyOut(items), d.accounts)}
      </Card.Content>
    </Card.Root>
  {:else}
    <Card.Root class="mb-6">
      <Card.Content>
        <p class="py-4 text-center text-sm text-muted-foreground">No recurring items yet. Add one above, pick from what's spotted in your history,
          or use ↻ on any transaction to start one from it.</p>
      </Card.Content>
    </Card.Root>
  {/if}

  {#if items.length}{@render suggestionsCard()}{/if}
{:catch err}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {err.message}</p>
      <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
    </Card.Content>
  </Card.Root>
{/await}
