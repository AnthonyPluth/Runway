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
  import type { RecurringItem as Item, RecurringValues, Suggestion } from "$lib/components/recurring/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, fmtDate, nb } from "$lib/format";
  import type { Account } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import { tick } from "svelte";
  import Plus from "@lucide/svelte/icons/plus";

  type Data = { accounts: Account[]; items: Item[]; suggestions: Suggestion[] };
  async function load(): Promise<Data> {
    const [accounts, items] = await Promise.all([api<Account[]>("/api/accounts"), api<Item[]>("/api/recurring")]);
    const suggestions = app.state?.connected ? await api<Suggestion[]>("/api/recurring/suggestions") : [];
    // The Add form starts open when there's nothing yet, with your primary account chosen.
    adding = !items.length;
    blank.account_id = app.state?.primary_account || accounts.find((a) => !a.hidden)?.id || "";
    return { accounts, items, suggestions };
  }
  let adding = $state(false);
  let blank: RecurringValues = $state({ name: "", account_id: "", amount: null, amount_mode: "fixed", frequency: "monthly", dates: "", anchor_date: "", match: "" });
  const data = load();
  let form = $state<HTMLElement | null>(null);

  // Money in and money out, by what the forecast expects (the usual amount, or the fixed one).
  const moneyIn = (items: Item[]) => items.filter((r) => (r.expected_amount ?? r.amount) > 0);
  const moneyOut = (items: Item[]) => items.filter((r) => !((r.expected_amount ?? r.amount) > 0));

  async function openForm() { adding = true; await tick(); form?.querySelector<HTMLInputElement>("input[name=name]")?.focus(); }
  async function add() {
    try {
      const r = await api<{ linked: number }>("/api/recurring", { method: "POST", body: { ...blank, active: 1 } });
      toast.success(r.linked ? `Added · matched ${r.linked} past transactions` : "Added"); reload();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function addSuggestion(s: Suggestion) {
    try { const r = await api<{ linked: number }>("/api/recurring", { method: "POST", body: s }); toast.success(`Added · matched ${r.linked}`); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
  function toggle(id: number, open: boolean) { if (open) openRecurring.add(String(id)); else openRecurring.delete(String(id)); }
</script>

<div class="mb-6 flex items-center justify-between gap-4">
  <p class="text-sm text-muted-foreground">The paychecks and bills the forecast expects.</p>
  <Button onclick={openForm}><Plus />Add</Button>
</div>

{#snippet group(title: string, list: Item[], accounts: Account[])}
  {#if list.length}
    <section aria-label={title}>
      <h2 class="mb-1 text-xs font-medium tracking-wider text-muted-foreground uppercase">{title}</h2>
      <div>
        {#each list as r (r.id)}
          <RecurringItem {r} {accounts} open={openRecurring.has(String(r.id))} ontoggle={(o) => toggle(r.id, o)} />
        {/each}
      </div>
    </section>
  {/if}
{/snippet}

{#await data}
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:then d}
  {#if adding}
    <Card.Root class="mb-6" bind:ref={form}>
      <Card.Header><Card.Title><h2>Add a recurring item</h2></Card.Title></Card.Header>
      <Card.Content>
        <RecurringFields bind:v={blank} accounts={d.accounts} />
        <div class="mt-4 flex items-center gap-2">
          <Button onclick={add}>Add</Button>
          {#if d.items.length}<Button variant="link" onclick={() => (adding = false)}>Cancel</Button>{/if}
        </div>
      </Card.Content>
    </Card.Root>
  {/if}

  {#if d.items.length}
    <Card.Root class="mb-6">
      <Card.Content class="flex flex-col gap-6">
        {@render group("Money in", moneyIn(d.items), d.accounts)}
        {@render group("Money out", moneyOut(d.items), d.accounts)}
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

  {#if d.suggestions.length}
    <Card.Root>
      <Card.Header><Card.Title><h2>Spotted in your history</h2></Card.Title></Card.Header>
      <Card.Content class="flex flex-col">
        {#each d.suggestions as s, i (i)}
          <div class="flex items-center gap-3 border-t py-3 first:border-t-0 first:pt-0">
            <RecIcon id={s.account_id} />
            <span class="flex min-w-0 flex-1 flex-col gap-0.5">
              <span class="truncate font-medium">{s.name}</span>
              <span class="text-xs text-muted-foreground">{nb(s.frequency)} · {nb(`${s.count}×`)} · {nb(`last ${fmtDate(s.anchor_date)}`)}</span>
            </span>
            <span class={cn("shrink-0 font-medium tabular-nums", s.amount > 0 && "text-emerald-500")}>{fmt(s.amount)}</span>
            <Button variant="outline" size="sm" aria-label={`Add ${s.name}`} onclick={() => addSuggestion(s)}>Add</Button>
          </div>
        {/each}
      </Card.Content>
    </Card.Root>
  {/if}
{:catch err}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {err.message}</p>
      <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
    </Card.Content>
  </Card.Root>
{/await}
