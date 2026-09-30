<script lang="ts">
    import { app } from "$lib/app.svelte";
    import * as Alert from "$lib/components/ui/alert";
  import * as Card from "$lib/components/ui/card";
  import { accountName } from "$lib/types";
    import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import AccountRow from "./AccountRow.svelte";
  import type { SettingsAccount } from "./types";
  import { linkCls } from "./ui";

  // Settings → Accounts: every account grouped by type (the forecast's account and length are set on Overview).
  let { accounts }: { accounts: SettingsAccount[] } = $props();

  const st = $derived(app.state!);
  const cash = $derived(accounts.filter((a) => a.kind === "checking" || a.kind === "savings"));
  const byName = $derived(Object.fromEntries(accounts.map((a) => [a.id, accountName(a)])));

  const KIND_GROUPS: [string, string[]][] = [["Cash", ["checking", "savings"]], ["Credit cards", ["credit"]], ["Loans", ["loan"]], ["Investments", ["investment"]]];
  const groups = $derived([
    ...KIND_GROUPS.map(([title, kinds]) => ({ title, list: accounts.filter((a) => !a.hidden && kinds.includes(a.kind)) })),
    { title: "Hidden", list: accounts.filter((a) => a.hidden) },
  ].filter((g) => g.list.length));

  const undecided = $derived(st.plaid_undecided ?? 0);
</script>

<p class="text-sm text-muted-foreground">The forecast’s account and length are set on <a class={linkCls} href="#overview">Overview</a>.</p>

{#if undecided}
  <Alert.Root>
    <TriangleAlert />
    <Alert.Description><p>
      {undecided === 1 ? "An account" : `${undecided} accounts`} from Plaid {undecided === 1 ? "is" : "are"} waiting for you to say what
      {undecided === 1 ? "it is" : "they are"}, so {undecided === 1 ? "it isn't" : "they aren't"} listed here or counted in net worth yet.
      <a class={linkCls} href="#setup/connections">Bank connections</a>
    </p></Alert.Description>
  </Alert.Root>
{/if}

<Card.Root>
  <Card.Header><Card.Title>Accounts</Card.Title></Card.Header>
  <Card.Content>
    {#each groups as g (g.title)}
      <section class="mb-4 last:mb-0" aria-label={g.title}>
        <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.title}</h3>
        {#each g.list as a (a.id)}<AccountRow {a} {cash} {byName} />{/each}
      </section>
    {:else}
      <p class="py-6 text-center text-sm text-muted-foreground">Accounts appear here after the first sync.</p>
    {/each}
  </Card.Content>
</Card.Root>
