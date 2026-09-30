<script lang="ts">
  import { api } from "$lib/api";
  import { loadCategories } from "$lib/categories.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import BankForm from "$lib/components/churning/BankForm.svelte";
  import BankList from "$lib/components/churning/BankList.svelte";
  import BestCard from "$lib/components/churning/BestCard.svelte";
  import CardForm from "$lib/components/churning/CardForm.svelte";
  import CardList from "$lib/components/churning/CardList.svelte";
  import Planned from "$lib/components/churning/Planned.svelte";
  import Rewards from "$lib/components/churning/Rewards.svelte";
  import Upcoming from "$lib/components/churning/Upcoming.svelte";
  import { BOTH, bankOrder, cardOrder, feesDue, five24Line, mine } from "$lib/components/churning/churning";
  import type { Churning, Wish } from "$lib/components/churning/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";

  // Churning: credit cards and bank accounts opened for their sign-up bonuses, for you and your partner. #churning
  // shows the cards, #churning/bank the bank account bonuses; the tiles and Upcoming cover both.
  let { sub = "" }: { sub?: string } = $props();
  const tab = $derived(sub === "bank" ? "bank" : "cards");

  let d = $state.raw<Churning | null>(null);
  let error = $state<string | null>(null);
  let version = $state(0);
  async function load() {
    try { d = await api<Churning>("/api/churning"); error = null; version++; }
    catch (err) { error = (err as Error).message; }
  }
  load();
  loadCategories().catch(() => {});

  // Whose cards to show: one person, or everyone (the default when there's more than one).
  let person = $state(BOTH);
  const people = $derived(d ? (person === BOTH ? d.people : [person]) : []);
  const cards = $derived(d ? [...mine(d.cards, person)].sort(cardOrder) : []);
  const bank = $derived(d ? [...mine(d.bank, person)].sort(bankOrder) : []);
  const upcoming = $derived(d ? mine(d.upcoming, person) : []);
  let showClosed = $state(false);
  const visibleCards = $derived(showClosed ? cards : cards.filter((c) => c.status === "open"));
  const visibleBank = $derived(showClosed ? bank : bank.filter((b) => b.state !== "closed"));

  const fees = $derived(d ? feesDue(cards.filter((c) => c.status === "open"), d.today) : { total: 0, count: 0 });
  const activeCards = $derived(cards.filter((c) => c.bonus_state === "active"));
  const activeBank = $derived(bank.filter((b) => b.state === "active" || b.state === "met"));
  const year = $derived(d?.today.slice(0, 4) ?? "");
  const bankThisYear = $derived(d ? people.reduce((s, p) => s + (d!.bank_income[p]?.[year] ?? 0), 0) : 0);
  const incomeYears = $derived(d ? [...new Set(people.flatMap((p) => Object.keys(d!.bank_income[p] ?? {})))].sort().reverse() : []);

  // The open form is remembered by id, so it keeps its place when the data behind it is reloaded (a benefit was marked
  // used, a plan checked off) and shows the card's new figures.
  let formId = $state<number | "new" | null>(null);
  let bankFormId = $state<number | "new" | null>(null);
  const form = $derived(formId === "new" ? "new" : (d?.cards.find((c) => c.id === formId) ?? null));
  const bankForm = $derived(bankFormId === "new" ? "new" : (d?.bank.find((b) => b.id === bankFormId) ?? null));
  const closeForm = (changed: boolean) => { formId = null; bankFormId = null; if (changed) load(); };
  // "I applied" on a planned item: the new card or bank bonus opens in its form, in its tab, to fill in the rest.
  async function applied(kind: Wish["kind"], id: number) {
    await load();
    showClosed = false;
    if (kind === "card") { bankFormId = null; formId = id; location.hash = "#churning"; }
    else { formId = null; bankFormId = id; location.hash = "#churning/bank"; }
  }
</script>

{#snippet tile(label: string, value: string, sub: string, tone = "")}
  <Card.Root class="gap-2">
    <Card.Header>
      <Card.Description>{label}</Card.Description>
      <Card.Title class={cn("text-2xl tabular-nums", tone)}>{value}</Card.Title>
    </Card.Header>
    <Card.Content class="text-sm text-muted-foreground">{sub}</Card.Content>
  </Card.Root>
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
  <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
    <h1 class="text-[34px] leading-tight font-bold tracking-tight">Churning</h1>
    {#if d.people.length > 1}
      <Segmented label="Whose" value={person || "all"} onchange={(v) => (person = v === "all" ? BOTH : v)}
        options={[...d.people.map((p) => ({ value: p, label: p })), { value: "all", label: "Both" }]} />
    {/if}
  </div>

  <div class="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
    {#if people.length === 1}
      {@const f = five24Line(d.five24[people[0]])}
      {@render tile(`${people[0]}'s 5/24`, f.count, f.next, d.five24[people[0]] && !d.five24[people[0]].under ? "text-[var(--warning)]" : "")}
    {:else}
      <Card.Root class="gap-2">
        <Card.Header><Card.Description>5/24</Card.Description></Card.Header>
        <Card.Content class="space-y-1.5">
          {#each people as p (p)}
            {@const f = five24Line(d.five24[p])}
            <div>
              <div class="flex items-baseline justify-between"><span>{p}</span><span class={cn("text-xl font-semibold tabular-nums", !d.five24[p]?.under && "text-[var(--warning)]")}>{f.count}</span></div>
              <div class="text-xs text-muted-foreground">{f.next}</div>
            </div>
          {/each}
        </Card.Content>
      </Card.Root>
    {/if}
    {@render tile("Annual fees, next 90 days", fmt0(fees.total), fees.count ? `${fees.count} card${fees.count === 1 ? "" : "s"}: keep, downgrade or close?` : "None due", fees.count ? "text-[var(--warning)]" : "")}
    {@render tile("Bonuses in progress", String(activeCards.length + activeBank.length),
      [activeCards.length ? `${activeCards.length} card${activeCards.length === 1 ? "" : "s"}, ${fmt0(activeCards.reduce((s, c) => s + Math.max(0, (c.bonus_spend ?? 0) - (c.spent ?? 0)), 0))} left to spend` : "",
       activeBank.length ? `${activeBank.length} bank, ${fmt0(activeBank.reduce((s, b) => s + b.bonus, 0))}` : ""].filter(Boolean).join(" · ") || "None right now")}
    {@render tile(`Bank bonuses in ${year}`, fmt0(bankThisYear), "Received so far; usually reported as taxable interest")}
  </div>

  <Upcoming items={upcoming} cards={cards.filter((c) => c.status === "open")} today={d.today} showOwner={people.length > 1} onchanged={load} />

  <Planned {d} {person} showOwner={people.length > 1} onchanged={load} onapplied={applied} />
  <BestCard {person} {version} showOwner={people.length > 1} />
  <Rewards {d} {people} onchanged={load} />

  <div class="flex flex-wrap items-center justify-between gap-3">
    <SubTabs label="Cards or bank bonuses" current={tab} class="mb-4"
      tabs={[{ id: "cards", href: "#churning", label: `Cards (${cards.length})` }, { id: "bank", href: "#churning/bank", label: `Bank bonuses (${bank.length})` }]} />
    <label class="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground"><input type="checkbox" class="size-4" bind:checked={showClosed} />Show closed</label>
  </div>

  {#if tab === "cards"}
    <Card.Root class="mb-6">
      <Card.Header>
        <Card.Title>Cards</Card.Title>
        <Card.Action><Button size="sm" onclick={() => (formId = "new")}>Add a card</Button></Card.Action>
      </Card.Header>
      <Card.Content>
        {#if form}{#key formId}<CardForm c={form === "new" ? null : form} {d} {person} onclose={closeForm} onchanged={load} />{/key}{/if}
        {#if visibleCards.length}
          <CardList cards={visibleCards} {d} showOwner={people.length > 1} onedit={(c) => (formId = c.id)} onchanged={load} />
        {:else}
          <p class="py-6 text-center text-sm text-muted-foreground">{cards.length ? "No open cards. Tick Show closed to see the rest." : "No cards yet. Add the cards you've opened in the last few years: 5/24 and the bonus rules need them."}</p>
        {/if}
        <details class="mt-4 text-sm text-muted-foreground">
          <summary class="cursor-pointer">About "Bonus again" and the banks' rules</summary>
          <p class="mt-2">These are rules of thumb as the churning community reports them. Banks change them, and each offer has its own terms: confirm with the bank before you apply. Set your own date on a card when you know better.</p>
          <ul class="mt-2 space-y-1">{#each d.issuers.filter((i) => i.key !== "other") as i (i.key)}<li><b class="font-medium text-foreground">{i.name}:</b> {i.rule}</li>{/each}</ul>
          <p class="mt-2">5/24 counts personal cards opened in the last 24 months, from any bank, closed or not. Authorized-user cards, business cards and product changes don't count here.</p>
        </details>
      </Card.Content>
    </Card.Root>
  {:else}
    <Card.Root class="mb-6">
      <Card.Header>
        <Card.Title>Bank bonuses</Card.Title>
        <Card.Action><Button size="sm" onclick={() => (bankFormId = "new")}>Add a bank bonus</Button></Card.Action>
      </Card.Header>
      <Card.Content>
        {#if bankForm}{#key bankFormId}<BankForm b={bankForm === "new" ? null : bankForm} {d} {person} onclose={closeForm} />{/key}{/if}
        {#if visibleBank.length}
          <BankList bonuses={visibleBank} {d} showOwner={people.length > 1} onedit={(b) => (bankFormId = b.id)} />
        {:else}
          <p class="py-6 text-center text-sm text-muted-foreground">{bank.length ? "Nothing open. Tick Show closed to see the rest." : "No bank bonuses yet."}</p>
        {/if}
        <p class="mt-4 text-xs text-muted-foreground">Linked to a Runway account, deposits categorized as income (or that look like payroll) count as direct deposits, and purchases as debit transactions. Banks decide what counts: check the offer's terms.</p>
      </Card.Content>
    </Card.Root>
    <Card.Root class="mb-6">
      <Card.Header>
        <Card.Title>Bonus money by year</Card.Title>
        <Card.Description>Banks usually report account bonuses as interest on a 1099-INT, so they're usually taxable. For your records, not tax advice.</Card.Description>
      </Card.Header>
      <Card.Content>
        {#if incomeYears.length}
          <table class="w-full max-w-md text-sm">
            <thead><tr class="text-xs text-muted-foreground [&>th]:py-1 [&>th]:font-normal"><th class="text-left">Year</th>{#each people as p (p)}<th class="text-right">{p}</th>{/each}</tr></thead>
            <tbody>
              {#each incomeYears as y (y)}
                <tr class="border-t [&>td]:py-1.5"><td>{y}</td>{#each people as p (p)}<td class="text-right tabular-nums">{fmt0(d.bank_income[p]?.[y] ?? 0)}</td>{/each}</tr>
              {/each}
            </tbody>
          </table>
        {:else}<p class="text-sm text-muted-foreground">No bank bonuses received yet.</p>{/if}
      </Card.Content>
    </Card.Root>
  {/if}
{/if}
