<script lang="ts">
  import { api } from "$lib/api";
  import { app } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { tick } from "svelte";
  import PlaidItemRow from "./PlaidItemRow.svelte";
  import PlaidKeys from "./PlaidKeys.svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import { connectPlaid, plaidSession } from "./plaid.svelte";
  import type { PlaidStatus } from "./types";
  import { helpCls } from "./ui";

  // Settings → Connections → Plaid: its API keys (collapsed once they're set), each bank it's connected to, and buttons to
  // connect another bank or card, or an investment account. The connections' accounts are matched to yours under Accounts.
  // The row says how many banks it's connected to, and turns amber (and opens) when one needs reconnecting or is a duplicate.
  const status = api<PlaidStatus>("/api/plaid/status");
  let st = $state<PlaidStatus | null>(null);
  let keysOpen = $state(false);
  status.then((s) => { st = s; keysOpen = !s.configured; }, () => {});
  let connecting = $state("");

  const items = $derived(st?.items ?? []);
  const trouble = $derived(items.some((it) => it.error || (it.duplicates ?? []).length));
  const count = $derived(items.length ? `${items.length} ${items.length === 1 ? "connection" : "connections"}` : undefined);
  // Open to start with until a bank is connected (through either service), and once Plaid's status says a connection
  // needs you.
  const startOpen = !app.state?.connected;

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

<ServiceRow name="Plaid" purpose="Banks, cards with statements and investment accounts, through Plaid Link" on={!!st?.configured}
  status={trouble ? "Needs attention" : count} warn={trouble} open={startOpen || trouble}>
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
</ServiceRow>
