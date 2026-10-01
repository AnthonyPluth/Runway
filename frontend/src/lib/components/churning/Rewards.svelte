<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt0 } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { currencyGroups, fullDate, points, valueSource } from "./churning";
  import CurrencySelect from "./CurrencySelect.svelte";
  import type { Churning } from "./types";

  // Points, in two views. Balances: what you have now (the balances you enter, each as of a day, worth what you set per point,
  // with an estimate of the balance today: yours plus what the cards earned since its day). Earned this year: what each
  // person's linked cards earned (estimated) and the bonuses, and what that's worth at the same values. They're kept apart
  // because points get spent: the year's earnings aren't a balance, so they never count toward what your balances are worth.
  let { d, people, onchanged }: { d: Churning; people: string[]; onchanged: () => void } = $props();
  let values = $state(false);
  let view = $state<"balances" | "earned">("balances");
  let newName = $state(""), newCents = $state("");
  const groups = $derived(currencyGroups(d));

  // A balance is as of today when you change it, unless you set its day yourself first.
  const key = (owner: string, currency: string) => `${owner}|${currency}`;
  const day = $state<Record<string, string>>({}), dayEdited = new Set<string>();
  const setBalance = (owner: string, currency: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    const k = key(owner, currency);
    await api("/api/churning/balances", { method: "POST", body: { owner, currency, points: f.value, as_of: dayEdited.has(k) && day[k] ? day[k] : d.today } });
    dayEdited.delete(k);
    onchanged();
  };
  // Takes a balance off the list (a program you no longer use); the row stays only while a card still earns in it.
  const remove = (owner: string, currency: string) => async () => {
    await api("/api/churning/balances", { method: "POST", body: { owner, currency, points: null } });
    onchanged();
  };
  // Changing the day keeps the balance and moves the day the estimate counts from.
  const setDay = (owner: string, currency: string, balance: number | null) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    if (balance == null) { dayEdited.add(key(owner, currency)); return; }
    await api("/api/churning/balances", { method: "POST", body: { owner, currency, points: balance, as_of: f.value || d.today } });
    onchanged();
  };
  const setCents = (k: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    await api("/api/churning/currencies", { method: "POST", body: { key: k, cents: f.value } });
    onchanged();
  };
  async function reset(k: string) {
    try { await api(`/api/churning/currencies/${encodeURIComponent(k)}/remove`, { method: "POST" }); onchanged(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function addCurrency() {
    try {
      await api("/api/churning/currencies", { method: "POST", body: { name: newName, cents: newCents } });
      newName = ""; newCents = ""; onchanged();
    } catch (err) { toast.error((err as Error).message); }
  }
  // A balance can be entered for any currency, even one no card of theirs earns yet.
  let addFor = $state<Record<string, string>>({});
</script>

<Card.Root class="mb-6">
  <Card.Header>
    <Card.Title>Rewards</Card.Title>
    <Card.Description>{view === "balances" ? "What you have now: the balances you enter, with an estimate of today's." : "What linked cards earned this year, estimated from their spending and earning rates, and bonuses."}</Card.Description>
    <Card.Action class="flex flex-wrap items-center gap-2">
      <Segmented label="Rewards view" bind:value={view} options={[{ value: "balances", label: "Balances" }, { value: "earned", label: "Earned this year" }]} />
      <Button size="sm" variant="outline" onclick={() => (values = !values)} aria-expanded={values}>Point values</Button>
    </Card.Action>
  </Card.Header>
  <Card.Content>
    {#if values}
      <div class="mb-5 rounded-lg bg-muted/40 p-4">
        <h3 class="mb-1 font-semibold">What a point is worth to you</h3>
        <p class="mb-3 text-sm text-muted-foreground">In cents. Point values are estimates; set your own. {d.values_note}</p>
        {#each groups as g (g.kind)}
          <h4 class="mt-3 mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">{g.label}</h4>
          <div class="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {#each g.currencies as c (c.key)}
              <div class="flex items-center gap-2 text-sm">
                <label class="flex flex-1 items-center justify-between gap-2"><span class="min-w-0">{c.name}
                  <span class="block text-xs text-muted-foreground" title={c.source_note}>{valueSource(c, d.values_as_of)}</span></span>
                  <span class="flex items-center gap-1"><input type="number" min="0" step="0.05" value={c.cents} use:autosave={setCents(c.key)}
                    class="h-8 w-20 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums" />¢</span>
                </label>
                <!-- Always takes the space, so the inputs line up whether or not a row has Remove or Reset. -->
                <span class="w-12 shrink-0">
                  {#if c.custom}<Button variant="link" size="sm" class="px-0" onclick={() => reset(c.key)}>Remove</Button>
                  {:else if c.default != null && c.cents !== c.default}<Button variant="link" size="sm" class="px-0" onclick={() => reset(c.key)} title={`Back to ${c.default}¢`}>Reset</Button>{/if}
                </span>
              </div>
            {/each}
          </div>
        {/each}
        <div class="mt-4 flex flex-wrap items-end gap-2">
          <label class="flex flex-col gap-1 text-sm">Add a currency<Input class="w-48" bind:value={newName} placeholder="e.g. Bilt Rewards" /></label>
          <label class="flex flex-col gap-1 text-sm">Cents a point<Input type="number" min="0" step="0.05" class="w-24" bind:value={newCents} /></label>
          <Button size="sm" variant="outline" onclick={addCurrency}>Add</Button>
        </div>
      </div>
    {/if}
    <div class="grid gap-6 lg:grid-cols-2">
      {#each people as person (person)}
        {@const r = d.rewards[person]}
        <div class="min-w-0">
          <div class="mb-1 flex items-baseline justify-between">
            <h3 class="font-semibold">{person}</h3>
            {#if view === "balances"}
              <span class="text-sm text-muted-foreground tabular-nums" title="What the balances you entered are worth">{fmt0(r?.balance_value ?? 0)}</span>
            {:else}
              <span class="text-sm text-muted-foreground tabular-nums" title="What this year's points and bonuses are worth at your point values, spent or not">{fmt0(r?.value ?? 0)}</span>
            {/if}
          </div>
          {#if view === "earned"}
            {@const earned = r?.currencies.filter((x) => x.earned + x.bonuses > 0) ?? []}
            {#if earned.length}
              <table class="w-full text-sm">
                <thead><tr class="text-xs text-muted-foreground [&>th]:py-1 [&>th]:font-normal">
                  <th class="text-left">Currency</th><th class="text-right">Spending</th><th class="text-right">Bonuses</th><th class="text-right">Total</th><th class="text-right">Worth</th>
                </tr></thead>
                <tbody>
                  {#each earned as row (row.currency)}
                    <tr class="border-t align-top [&>td]:py-1.5">
                      <td class="pr-2">{row.name}</td>
                      <td class="text-right tabular-nums">{row.earned ? `~${points(row.earned)}` : "—"}</td>
                      <td class="text-right tabular-nums">{row.bonuses ? points(row.bonuses) : "—"}</td>
                      <td class="text-right tabular-nums">~{points(row.earned + row.bonuses)}</td>
                      <td class="text-right tabular-nums" title={`At ${row.cents}¢ a point`}>~{fmt0(row.value)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {:else}<p class="text-sm text-muted-foreground">Nothing earned yet this year.</p>{/if}
          {:else if r?.currencies.length}
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-muted-foreground [&>th]:py-1 [&>th]:font-normal">
                <th class="text-left">Currency</th><th class="text-right">Balance</th><th class="text-right">Worth</th>
              </tr></thead>
              <tbody>
                {#each r.currencies as row (row.currency)}
                  {@const k = key(person, row.currency)}
                  <tr class="border-t align-top [&>td]:py-1.5">
                    <td class="pr-2">{row.name}</td>
                    <td class="text-right">
                      <input type="number" min="0" step="1" value={row.balance ?? ""} placeholder="—" aria-label={`${person}'s ${row.name} balance`}
                        use:autosave={setBalance(person, row.currency)} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums" />
                      {#if row.balance != null}
                        <label class="mt-1 flex items-center justify-end gap-1 text-xs text-muted-foreground">as of
                          <input type="date" max={d.today} value={row.as_of ?? d.today} aria-label={`Day of ${person}'s ${row.name} balance`}
                            oninput={(e) => (day[k] = e.currentTarget.value)} use:autosave={setDay(person, row.currency, row.balance)}
                            class="h-7 w-32 rounded-md border border-input bg-transparent px-1.5 text-xs" />
                        </label>
                        <button type="button" class="mt-1 text-xs text-muted-foreground underline hover:text-foreground"
                          aria-label={`Remove ${person}'s ${row.name} balance`} onclick={remove(person, row.currency)}>Remove</button>
                        {#if row.est_balance != null}
                          <div class="mt-1 text-xs text-muted-foreground" title={`Your balance plus the ${points(row.earned_since)} points your linked cards earned since ${row.as_of ? fullDate(row.as_of) : "then"}, at their normal rates. An estimate: redemptions and portal bookings aren't counted.`}>
                            Estimated now: <span class="tabular-nums">~{row.est_balance.toLocaleString("en-US")}</span> <span class="italic">(+{points(row.earned_since)} earned since)</span>
                          </div>
                        {/if}
                      {/if}
                    </td>
                    <td class="text-right tabular-nums">{row.balance_value == null ? "—" : fmt0(row.balance_value)}
                      {#if row.est_value != null}<div class="mt-1 text-xs text-muted-foreground" title="Worth of the estimated balance">~{fmt0(row.est_value)} est.</div>{/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}<p class="text-sm text-muted-foreground">Nothing yet.</p>{/if}
          {#if view === "balances"}<div class="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <CurrencySelect {d} blank="Add a balance…" bind:value={() => addFor[person] ?? "", (v) => (addFor[person] = v)} aria-label={`Add a balance for ${person}`}
              only={(c) => !r?.currencies.some((x) => x.currency === c)} class="h-8 py-0" />
            {#if addFor[person]}
              <input type="number" min="0" step="1" placeholder="points" aria-label="Balance"
                use:autosave={setBalance(person, addFor[person])} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm" />
              <label class="flex items-center gap-1 text-xs text-muted-foreground">as of
                <input type="date" max={d.today} value={d.today} aria-label={`Day of the new ${person} balance`}
                  oninput={(e) => (day[key(person, addFor[person])] = e.currentTarget.value)}
                  use:autosave={async () => { dayEdited.add(key(person, addFor[person])); }}
                  class="h-7 w-32 rounded-md border border-input bg-transparent px-1.5 text-xs" />
              </label>
            {/if}
          </div>{/if}
        </div>
      {/each}
    </div>
  </Card.Content>
</Card.Root>
