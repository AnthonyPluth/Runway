<script lang="ts">
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import * as Card from "$lib/components/ui/card";
  import { tick } from "svelte";
  import ConnectBank from "./ConnectBank.svelte";
  import PlaidItemRow from "./PlaidItemRow.svelte";
  import PlaidKeys from "./PlaidKeys.svelte";
  import { plaidSession } from "./plaid.svelte";
  import SimpleFinRow from "./SimpleFinRow.svelte";
  import type { PlaidStatus } from "./types";
  import { helpCls } from "./ui";

  // Settings → Bank connections: one list of where Runway's bank data comes from (SimpleFIN and each Plaid connection) and
  // one Connect a bank button. Which of your accounts each bank account is, is decided under Accounts.
  const status = api<PlaidStatus>("/api/plaid/status");
  // The Plaid API keys stay collapsed once they're set; Connect a bank opens them when they aren't.
  let keysOpen = $state(false);
  status.then((st) => { keysOpen = !st.configured; }, () => {});
  async function showKeys() {
    keysOpen = true;
    await tick();
    document.getElementById("plaid-keys")?.scrollIntoView({ block: "nearest" });
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>Bank connections</Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#await status}
      <p class="text-sm text-muted-foreground">Loading…</p>
    {:then st}
      {#if app.state?.simplefin || st.items.length}
        <div class="flex flex-col gap-3">
          {#if app.state?.simplefin}<SimpleFinRow />{/if}
          {#each st.items as it (it.item_id)}<PlaidItemRow {it} items={st.items} />{/each}
        </div>
        <div><ConnectBank variant="outline" configured={st.configured} onkeys={showKeys} /></div>
      {:else}
        <div class="flex flex-col items-center gap-3 rounded-xl border border-dashed px-4 py-8 text-center">
          <p class="font-medium">Nothing is connected yet</p>
          <p class={helpCls}>Connect a bank to bring in your accounts and transactions.</p>
          <ConnectBank configured={st.configured} onkeys={showKeys} />
        </div>
      {/if}

      {#if plaidSession.last}
        {@const l = plaidSession.last}
        <p class={helpCls}>Last Link attempt ({l.at}): Link Session ID <code class="rounded bg-muted px-1 text-foreground select-all">{l.sid}</code>{#if l.request}{" · Request ID "} <code class="rounded bg-muted px-1 text-foreground select-all">{l.request}</code>{/if}</p>
      {/if}
      <PlaidKeys {st} bind:open={keysOpen} />
    {:catch err}
      <p class="text-sm text-muted-foreground">{err.message}</p>
    {/await}
  </Card.Content>
</Card.Root>
