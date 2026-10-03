<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, nb } from "$lib/format";
  import BankIcon from "./BankIcon.svelte";
  import PlaidChoice from "./PlaidChoice.svelte";
  import { addPlaidAccounts, matchPlaidAccount } from "./plaid.svelte";
  import type { PlaidBankAccount } from "./plaidAccounts";
  import type { SettingsAccount } from "./types";
  import { linkCls } from "./ui";

  // Plaid bank, card and investment accounts nobody has decided about yet, at the top of Accounts: each is added as its
  // own account (chosen to start with: Add, or Add all), matched to one you already have, or left out. Once chosen it
  // moves into its type group below. Ones you left out stay reachable.
  let { waiting, left, mine }: { waiting: PlaidBankAccount[]; left: PlaidBankAccount[]; mine: SettingsAccount[] } = $props();
  let showLeft = $state(false);
  let adding = $state(false);
  async function add(ids: string[]) {
    adding = true;
    try { if (ids.length === 1) await matchPlaidAccount(ids[0], "new"); else await addPlaidAccounts(ids); }
    finally { adding = false; }
  }
</script>

{#snippet row({ p, it }: PlaidBankAccount, undecided = false)}
  <div class="flex flex-wrap items-center gap-3 border-b py-2.5 last:border-b-0">
    <BankIcon name={it.institution_name} />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="truncate font-medium">{p.name || p.official_name || "Account"}{#if p.mask}{" "}<span class="font-normal text-muted-foreground">••{p.mask}</span>{/if}</span>
      <span class="text-xs text-muted-foreground">{nb(`${it.institution_name || "Plaid"} · ${p.subtype || p.type || "account"} · ${fmt(p.balance)}`)}</span>
    </span>
    {#if undecided}
      <span class="flex w-full items-center gap-2 sm:ml-auto sm:w-auto">
        <PlaidChoice {p} {it} {mine} preselect class="min-w-0 flex-1" />
        <Button variant="outline" size="sm" class="shrink-0" disabled={adding} aria-label={`Add ${p.name || p.official_name || "this account"}`}
          onclick={() => add([p.id])}>Add</Button>
      </span>
    {:else}<PlaidChoice {p} {it} {mine} class="sm:ml-auto" />{/if}
  </div>
{/snippet}

{#if waiting.length}
  <Card.Root>
    <Card.Header class="px-4 sm:px-6"><Card.Title>New from Plaid</Card.Title>
      {#if waiting.length > 1}<Card.Action><Button size="sm" disabled={adding} onclick={() => add(waiting.map((w) => w.p.id))}>Add all {waiting.length}</Button></Card.Action>{/if}
    </Card.Header>
    <Card.Content class="px-4 sm:px-6">
      <section aria-label="New from Plaid">{#each waiting as w (w.p.id)}{@render row(w, true)}{/each}</section>
    </Card.Content>
  </Card.Root>
{/if}

{#if left.length}
  <p class="text-sm text-muted-foreground">{left.length === 1 ? "1 Plaid account you're not using" : `${left.length} Plaid accounts you're not using`} ·
    <button type="button" class={linkCls} aria-expanded={showLeft} onclick={() => (showLeft = !showLeft)}>{showLeft ? "Hide" : "Show"}</button></p>
  {#if showLeft}<div class="rounded-xl border px-3 py-1 text-sm">{#each left as w (w.p.id)}{@render row(w)}{/each}</div>{/if}
{/if}
