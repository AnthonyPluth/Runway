<script lang="ts">
  import { api } from "$lib/api";
  import * as Card from "$lib/components/ui/card";
  import { accountName } from "$lib/types";
  import AccountRow from "./AccountRow.svelte";
  import NewFromPlaid from "./NewFromPlaid.svelte";
  import { ignoredAccounts, undecidedAccounts } from "./plaidAccounts";
  import type { PlaidStatus, SettingsAccount } from "./types";
  import { linkCls } from "./ui";

  // Settings → Accounts: every account grouped by type (the forecast's account and length are set on Overview).
  let { accounts }: { accounts: SettingsAccount[] } = $props();

  const cash = $derived(accounts.filter((a) => a.kind === "checking" || a.kind === "savings"));
  const byName = $derived(Object.fromEntries(accounts.map((a) => [a.id, accountName(a)])));

  const KIND_GROUPS: [string, string[]][] = [["Cash", ["checking", "savings"]], ["Credit cards", ["credit"]], ["Loans", ["loan"]], ["Investments", ["investment"]]];
  const groups = $derived([
    ...KIND_GROUPS.map(([title, kinds]) => ({ title, list: accounts.filter((a) => !a.hidden && kinds.includes(a.kind)) })),
    { title: "Hidden", list: accounts.filter((a) => a.hidden) },
  ].filter((g) => g.list.length));

  // Plaid's accounts, for the "New from Plaid" group and for linking an account to one. Without Plaid this just stays null.
  let plaid = $state<PlaidStatus | null>(null);
  api<PlaidStatus>("/api/plaid/status").then((r) => (plaid = r), () => {});
  const waiting = $derived(undecidedAccounts(plaid));
  const mine = $derived(accounts.filter((a) => !a.id.startsWith("pl:") && ["checking", "savings", "credit", "loan"].includes(a.kind)));
</script>

<p class="text-sm text-muted-foreground">The forecast’s account and length are set on <a class={linkCls} href="#overview">Overview</a>.</p>

<NewFromPlaid {waiting} left={ignoredAccounts(plaid)} {mine} />

<Card.Root>
  <Card.Header><Card.Title>Accounts</Card.Title></Card.Header>
  <Card.Content>
    {#each groups as g (g.title)}
      <section class="mb-4 last:mb-0" aria-label={g.title}>
        <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.title}</h3>
        {#each g.list as a (a.id)}<AccountRow {a} {cash} {byName} {plaid} {mine} />{/each}
      </section>
    {:else}
      <p class="py-6 text-center text-sm text-muted-foreground">Accounts appear here after the first sync.</p>
    {/each}
  </Card.Content>
</Card.Root>
