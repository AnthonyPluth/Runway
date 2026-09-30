<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { fmt, nb } from "$lib/format";
  import BankIcon from "./BankIcon.svelte";
  import PlaidChoice from "./PlaidChoice.svelte";
  import type { PlaidBankAccount } from "./plaidAccounts";
  import type { SettingsAccount } from "./types";

  // Plaid bank, card and investment accounts nobody has decided about yet, at the top of Accounts: each is added as its own account, matched to
  // one you already have, or left out. Once chosen it moves into its type group below. Ones you left out stay reachable.
  let { waiting, left, mine }: { waiting: PlaidBankAccount[]; left: PlaidBankAccount[]; mine: SettingsAccount[] } = $props();
</script>

{#snippet row({ p, it }: PlaidBankAccount)}
  <div class="flex flex-wrap items-center gap-3 border-b py-2.5 last:border-b-0">
    <BankIcon name={it.institution_name} />
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="truncate font-medium">{p.name || p.official_name || "Account"}{#if p.mask}{" "}<span class="font-normal text-muted-foreground">••{p.mask}</span>{/if}</span>
      <span class="text-xs text-muted-foreground">{nb(`${it.institution_name || "Plaid"} · ${p.subtype || p.type || "account"} · ${fmt(p.balance)}`)}</span>
    </span>
    <PlaidChoice {p} {it} {mine} class="sm:ml-auto" />
  </div>
{/snippet}

{#if waiting.length}
  <Card.Root>
    <Card.Header><Card.Title>New from Plaid — choose what to do</Card.Title></Card.Header>
    <Card.Content>
      <section aria-label="New from Plaid">{#each waiting as w (w.p.id)}{@render row(w)}{/each}</section>
    </Card.Content>
  </Card.Root>
{/if}

{#if left.length}
  <details class="rounded-xl border px-3 py-2.5 text-sm">
    <summary class="cursor-pointer text-muted-foreground">{left.length === 1 ? "1 Plaid account you're not using" : `${left.length} Plaid accounts you're not using`}</summary>
    <div class="mt-1">{#each left as w (w.p.id)}{@render row(w)}{/each}</div>
  </details>
{/if}
