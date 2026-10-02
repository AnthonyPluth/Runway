<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { fmt0 } from "$lib/format";
  import { cn } from "$lib/utils";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { toast } from "svelte-sonner";
  import { balanceText, balanceValue, currencyGroups, fullDate, points, valueSource } from "./churning";
  import CurrencySelect from "./CurrencySelect.svelte";
  import Section from "./Section.svelte";
  import type { Churning } from "./types";

  // Points, in two views. Balances: what you have now (the balances you enter, each as of a day, worth what you set per point,
  // with an estimate of the balance today: yours plus what the cards earned since its day). Earned this year: what each
  // person's linked cards earned (estimated) and the bonuses, and what that's worth at the same values. They're kept apart
  // because points get spent: the year's earnings aren't a balance, so they never count toward what your balances are worth.
  let { d, people, onchanged }: { d: Churning; people: string[]; onchanged: () => void } = $props();
  let values = $state(false);
  let rowOpen = $state<string | null>(null);
  let view = $state<"balances" | "earned">("balances");
  let newName = $state(""), newCents = $state("");
  const groups = $derived(currencyGroups(d));

  // A balance is as of the day you enter it: the estimate of it now counts what the cards earned from then on.
  const key = (owner: string, currency: string) => `${owner}|${currency}`;
  // A balance reads with its commas ("125,000"), which a number field can't show, and is a number field (arrows that
  // step it by a point) while you edit it.
  function commas(el: HTMLInputElement) {
    const show = () => {
      const n = balanceValue(el.value);
      if (n === "" || !Number.isFinite(Number(n))) return;   // left as typed: what's wrong with it shows when it saves
      el.type = "text";
      el.value = balanceText(Number(n));
    };
    const edit = () => { el.value = balanceValue(el.value); el.type = "number"; };
    el.addEventListener("focus", edit);
    el.addEventListener("blur", show);
    return { destroy() { el.removeEventListener("focus", edit); el.removeEventListener("blur", show); } };
  }
  const setBalance = (owner: string, currency: string) => async (f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) => {
    await api("/api/churning/balances", { method: "POST", body: { owner, currency, points: balanceValue(f.value), as_of: d.today } });
    onchanged();
  };
  // Takes a balance off the list (a program you no longer use); the row stays only while a card still earns in it.
  const remove = (owner: string, currency: string) => async () => {
    await api("/api/churning/balances", { method: "POST", body: { owner, currency, points: null } });
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
    <Card.Action class="flex flex-wrap items-center gap-2">
      <Segmented label="Rewards view" bind:value={view} options={[{ value: "balances", label: "Balances" }, { value: "earned", label: "Earned this year" }]} />
    </Card.Action>
  </Card.Header>
  <Card.Content>
    <Section id="values" title="Point values" summary="What a point is worth to you, in cents" bind:open={values}>
      <div>
        {#each groups as g (g.kind)}
          <!-- Set once and rarely touched: one dense line a currency, where it came from in small print beside the name. -->
          <h4 class="mt-2 mb-0.5 text-[11px] font-semibold tracking-[0.14em] text-muted-foreground uppercase first:mt-0">{g.label}</h4>
          <div class="grid gap-x-6 sm:grid-cols-2 xl:grid-cols-3">
            {#each g.currencies as c (c.key)}
              <div class="flex min-h-8 items-center gap-1.5 text-sm">
                <label class="flex min-w-0 flex-1 items-center gap-1.5"><span class="min-w-0 truncate" title={c.name}>{c.name}</span>
                  <span class="shrink-0 text-[11px] text-muted-foreground" title={c.source_note}>{valueSource(c, d.values_as_of)}</span>
                  <span class="ml-auto flex shrink-0 items-center gap-0.5"><input type="number" min="0" step="0.05" value={c.cents} use:autosave={setCents(c.key)}
                    aria-label={`Cents a ${c.name} point`} class="h-7 w-14 rounded-md border border-transparent bg-transparent px-1.5 text-right text-sm tabular-nums hover:border-input focus-visible:border-ring" />¢</span>
                </label>
                <!-- Always takes the space, so the inputs line up whether or not a row has Remove or Reset. -->
                <span class="w-11 shrink-0 text-xs">
                  {#if c.custom}<Button variant="link" size="sm" class="h-auto px-0 text-xs" onclick={() => reset(c.key)}>Remove</Button>
                  {:else if c.default != null && c.cents !== c.default}<Button variant="link" size="sm" class="h-auto px-0 text-xs" onclick={() => reset(c.key)} title={`Back to ${c.default}¢`}>Reset</Button>{/if}
                </span>
              </div>
            {/each}
          </div>
        {/each}
        <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <Input class="h-8 w-48" bind:value={newName} placeholder="Add a currency, e.g. Bilt" aria-label="Add a currency" />
          <span class="flex items-center gap-0.5"><Input type="number" min="0" step="0.05" class="h-8 w-20" bind:value={newCents} placeholder="cents" aria-label="Cents a point" />¢</span>
          <Button size="sm" variant="outline" onclick={addCurrency}>Add</Button>
        </div>
      </div>
    </Section>
    <div class="mt-5 grid gap-6 lg:grid-cols-2">
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
            {/if}
          {:else if r?.currencies.length}
            <table class="w-full text-sm">
              <thead><tr class="text-xs text-muted-foreground [&>th]:py-1 [&>th]:font-normal">
                <th class="text-left">Currency</th><th class="text-right">Balance</th><th class="text-right">Worth</th>
              </tr></thead>
              <tbody>
                {#each r.currencies as row (row.currency)}
                  {@const k = key(person, row.currency)}
                  <tr class="border-t align-top [&>td]:py-1.5">
                    <td class="pr-2">
                      {#if row.balance != null}
                        <button type="button" class="inline-flex cursor-pointer items-center gap-1 text-left" aria-expanded={rowOpen === k} aria-label={`${row.name} details`}
                          onclick={() => (rowOpen = rowOpen === k ? null : k)}>{row.name}<ChevronRight class={cn("size-3.5 text-muted-foreground transition-transform", rowOpen === k && "rotate-90")} aria-hidden="true" /></button>
                      {:else}{row.name}{/if}
                    </td>
                    <td class="text-right">
                      <input type="text" inputmode="numeric" min="0" step="1" value={balanceText(row.balance)} placeholder="—" aria-label={`${person}'s ${row.name} balance`}
                        use:commas use:autosave={setBalance(person, row.currency)} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm tabular-nums" />
                    </td>
                    <td class="text-right tabular-nums">{row.balance_value == null ? "—" : fmt0(row.balance_value)}</td>
                  </tr>
                  {#if row.balance != null && rowOpen === k}
                    <tr class="[&>td]:pb-2">
                      <td colspan="3">
                        <div class="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 text-xs text-muted-foreground">
                          {#if row.est_balance != null}
                            <span title={`Your balance plus the ${points(row.earned_since)} points your linked cards earned since ${row.as_of ? fullDate(row.as_of) : "then"}, at their normal rates. An estimate: redemptions and portal bookings aren't counted.`}>
                              Estimated now: <span class="tabular-nums">~{row.est_balance.toLocaleString("en-US")}</span> <span class="italic">(+{points(row.earned_since)} earned since)</span>{#if row.est_value != null}<span class="tabular-nums" title="Worth of the estimated balance"> · ~{fmt0(row.est_value)}</span>{/if}
                            </span>
                          {/if}
                          {#if row.as_of}<span>Entered {fullDate(row.as_of)}</span>{/if}
                          <button type="button" class="underline hover:text-foreground"
                            aria-label={`Remove ${person}'s ${row.name} balance`} onclick={remove(person, row.currency)}>Remove</button>
                        </div>
                      </td>
                    </tr>
                  {/if}
                {/each}
              </tbody>
            </table>
          {/if}
          {#if view === "balances"}<div class="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <CurrencySelect {d} blank="Add a balance…" bind:value={() => addFor[person] ?? "", (v) => (addFor[person] = v)} aria-label={`Add a balance for ${person}`}
              only={(c) => !r?.currencies.some((x) => x.currency === c)} class="h-8 py-0" />
            {#if addFor[person]}
              <input type="text" inputmode="numeric" min="0" step="1" placeholder="points" aria-label="Balance"
                use:commas use:autosave={setBalance(person, addFor[person])} class="h-8 w-28 rounded-md border border-input bg-transparent px-2 text-right text-sm" />
            {/if}
          </div>{/if}
        </div>
      {/each}
    </div>
  </Card.Content>
</Card.Root>
