<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { fmt0 } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { currencyGroups, fullDate, points, valueSource } from "./churning";
  import CurrencySelect from "./CurrencySelect.svelte";
  import type { Churning } from "./types";

  // Points: what each person's linked cards earned this year (estimated, in points), bonuses earned this year, and the
  // balances you enter, each as of a day. Points get spent, so what they're worth comes only from the balance you
  // enter, at the value you set for that currency (which also feeds the best-card estimates); the year's earnings are
  // never counted. Under a balance, an estimate of what it is now: that balance plus what the cards earned since its day.
  let { d, people, onchanged }: { d: Churning; people: string[]; onchanged: () => void } = $props();
  let values = $state(false);
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
    <Card.Description>This year's points are estimated from linked cards' spending and earning rates.</Card.Description>
    <Card.Action><Button size="sm" variant="outline" onclick={() => (values = !values)} aria-expanded={values}>Point values</Button></Card.Action>
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
                {#if c.custom}<Button variant="link" size="sm" class="px-0" onclick={() => reset(c.key)}>Remove</Button>
                {:else if c.default != null && c.cents !== c.default}<Button variant="link" size="sm" class="px-0" onclick={() => reset(c.key)} title={`Back to ${c.default}¢`}>Reset</Button>{/if}
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
            <span class="text-sm text-muted-foreground tabular-nums" title="What the balances you entered are worth">{fmt0(r?.balance_value ?? 0)}</span>
          </div>
          {#if r?.currencies.length}
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-muted-foreground [&>th]:py-1 [&>th]:font-normal">
                <th class="text-left">Currency</th><th class="text-right">This year</th><th class="text-right">Balance</th><th class="text-right">Worth</th>
              </tr></thead>
              <tbody>
                {#each r.currencies as row (row.currency)}
                  {@const k = key(person, row.currency)}
                  <tr class="border-t align-top [&>td]:py-1.5">
                    <td class="pr-2">{row.name}</td>
                    <td class="text-right tabular-nums" title={row.bonuses ? `${points(row.earned)} from spending, ${points(row.bonuses)} from bonuses` : undefined}>{row.earned + row.bonuses ? `~${points(row.earned + row.bonuses)}` : "—"}</td>
                    <td class="text-right">
                      <input type="number" min="0" step="1" value={row.balance ?? ""} placeholder="—" aria-label={`${person}'s ${row.name} balance`}
                        use:autosave={setBalance(person, row.currency)} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums" />
                      {#if row.balance != null}
                        <label class="mt-1 flex items-center justify-end gap-1 text-xs text-muted-foreground">as of
                          <input type="date" max={d.today} value={row.as_of ?? d.today} aria-label={`Day of ${person}'s ${row.name} balance`}
                            oninput={(e) => (day[k] = e.currentTarget.value)} use:autosave={setDay(person, row.currency, row.balance)}
                            class="h-7 w-32 rounded-md border border-input bg-transparent px-1.5 text-xs" />
                        </label>
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
          <div class="mt-2 flex flex-wrap items-center gap-2 text-sm">
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
          </div>
        </div>
      {/each}
    </div>
  </Card.Content>
</Card.Root>
