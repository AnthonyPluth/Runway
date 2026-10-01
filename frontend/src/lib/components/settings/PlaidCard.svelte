<script lang="ts">
  import { api } from "$lib/api";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { tick } from "svelte";
  import PlaidItemRow from "./PlaidItemRow.svelte";
  import PlaidKeys from "./PlaidKeys.svelte";
  import { connectPlaid, plaidSession } from "./plaid.svelte";
  import type { PlaidStatus } from "./types";
  import { helpCls } from "./ui";

  // Settings → Connections → Plaid: its API keys (collapsed once they're set), each bank it's connected to, and buttons to
  // connect another bank or card, or an investment account. The connections' accounts are matched to yours under Accounts.
  const status = api<PlaidStatus>("/api/plaid/status");
  let keysOpen = $state(false);
  status.then((st) => { keysOpen = !st.configured; }, () => {});
  let connecting = $state("");

  async function connect(kind: string) {
    connecting = kind;
    await connectPlaid(kind);
    connecting = "";
  }
  async function showKeys() {
    keysOpen = true;
    await tick();
    document.getElementById("plaid-keys")?.scrollIntoView({ block: "nearest" });
  }
</script>

<Card.Root>
  <Card.Header>
    <Card.Title>Plaid</Card.Title>
    <Card.Description>Banks, credit cards (with statements) and investment accounts, through Plaid Link, with up to two years of history.</Card.Description>
  </Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#await status}
      <p class="text-sm text-muted-foreground">Loading…</p>
    {:then st}
      {#if st.items.length}
        <div class="flex flex-col gap-3">
          {#each st.items as it (it.item_id)}<PlaidItemRow {it} items={st.items} />{/each}
        </div>
      {/if}
      {#if st.configured}
        <div class="flex flex-wrap gap-2">
          <Button variant={st.items.length ? "outline" : "default"} disabled={!!connecting} onclick={() => connect("bank")}>Connect a bank or card</Button>
          <Button variant="outline" disabled={!!connecting} onclick={() => connect("investments")}>Connect an investment account</Button>
        </div>
      {:else}
        <p class={helpCls}>Plaid needs API keys first: a client ID and secret from your Plaid dashboard.
          {#if !keysOpen}<button type="button" class="font-medium text-foreground underline underline-offset-4" onclick={showKeys}>Add them</button>{/if}</p>
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
