<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { fmt0 } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { points } from "./churning";
  import type { Churning } from "./types";

  // Points: what each person's linked cards earned this year (estimated), bonuses earned this year, balances you
  // enter, and what they're worth. Point values are yours to set; they feed every estimate on the page.
  let { d, people, onchanged }: { d: Churning; people: string[]; onchanged: () => void } = $props();
  let values = $state(false);
  let newName = $state(""), newCents = $state("");

  const setBalance = (owner: string, currency: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    await api("/api/churning/balances", { method: "POST", body: { owner, currency, points: f.value } });
    onchanged();
  };
  const setCents = (key: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    await api("/api/churning/currencies", { method: "POST", body: { key, cents: f.value } });
    onchanged();
  };
  async function reset(key: string) {
    try { await api(`/api/churning/currencies/${encodeURIComponent(key)}/remove`, { method: "POST" }); onchanged(); }
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
        <p class="mb-3 text-sm text-muted-foreground">In cents. The defaults are modest; change them to match how you redeem.</p>
        <div class="grid gap-2 sm:grid-cols-2">
          {#each d.currencies as c (c.key)}
            <div class="flex items-center gap-2 text-sm">
              <label class="flex flex-1 items-center justify-between gap-2">{c.name}
                <span class="flex items-center gap-1"><input type="number" min="0" step="0.05" value={c.cents} use:autosave={setCents(c.key)}
                  class="h-8 w-20 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums" />¢</span>
              </label>
              {#if c.custom}<Button variant="link" size="sm" class="px-0" onclick={() => reset(c.key)}>Remove</Button>
              {:else if c.default != null && c.cents !== c.default}<Button variant="link" size="sm" class="px-0" onclick={() => reset(c.key)} title={`Back to ${c.default}¢`}>Reset</Button>{/if}
            </div>
          {/each}
        </div>
        <div class="mt-3 flex flex-wrap items-end gap-2">
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
            <span class="text-sm text-muted-foreground tabular-nums">{fmt0((r?.value ?? 0) + (r?.balance_value ?? 0))}</span>
          </div>
          {#if r?.currencies.length}
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-muted-foreground [&>th]:py-1 [&>th]:font-normal">
                <th class="text-left">Currency</th><th class="text-right">This year</th><th class="text-right">Balance</th><th class="text-right">Worth</th>
              </tr></thead>
              <tbody>
                {#each r.currencies as row (row.currency)}
                  <tr class="border-t [&>td]:py-1.5">
                    <td class="pr-2">{row.name}</td>
                    <td class="text-right tabular-nums" title={row.bonuses ? `${points(row.earned)} from spending, ${points(row.bonuses)} from bonuses` : undefined}>{row.earned + row.bonuses ? `~${points(row.earned + row.bonuses)}` : "—"}</td>
                    <td class="text-right">
                      <input type="number" min="0" step="100" value={row.balance ?? ""} placeholder="—" aria-label={`${person}'s ${row.name} balance`}
                        use:autosave={setBalance(person, row.currency)} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums" />
                    </td>
                    <td class="text-right tabular-nums">{fmt0(row.value + (row.balance_value ?? 0))}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}<p class="text-sm text-muted-foreground">Nothing yet.</p>{/if}
          <div class="mt-2 flex items-center gap-2 text-sm">
            <select class="h-8 rounded-md border border-input bg-transparent px-2 text-sm dark:bg-input" aria-label={`Add a balance for ${person}`}
              value={addFor[person] ?? ""} onchange={(e) => (addFor[person] = e.currentTarget.value)}>
              <option value="">Add a balance…</option>
              {#each d.currencies.filter((c) => !r?.currencies.some((x) => x.currency === c.key)) as c (c.key)}<option value={c.key}>{c.name}</option>{/each}
            </select>
            {#if addFor[person]}
              <input type="number" min="0" step="100" placeholder="points" aria-label="Balance"
                use:autosave={setBalance(person, addFor[person])} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm" />
            {/if}
          </div>
        </div>
      {/each}
    </div>
  </Card.Content>
</Card.Root>
