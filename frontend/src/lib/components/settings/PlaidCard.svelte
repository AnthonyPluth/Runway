<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { toast } from "svelte-sonner";
  import PlaidItemRow from "./PlaidItemRow.svelte";
  import { openPlaidLink, plaidSession } from "./plaid.svelte";
  import type { PlaidStatus, SettingsAccount } from "./types";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls, selectCls } from "./ui";

  // Plaid: your connections (each with its accounts matched to yours), the buttons to add one, and the API keys.
  let { accounts }: { accounts: SettingsAccount[] } = $props();
  const status = api<PlaidStatus>("/api/plaid/status");

  const saveKeys = (env: string, clientId: string) => api("/api/plaid/settings", { method: "POST", body: { env, client_id: clientId } });
  let env = $state("production");
  let clientId = $state("");
  status.then((st) => { env = st.env === "sandbox" ? "sandbox" : "production"; clientId = st.client_id; }, () => {});

  async function saveSecret(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/plaid/settings", { method: "POST", body: { secret: f.value } });
    toast.success("Plaid secret saved"); reload();
  }
  let connecting = $state("");
  async function connect(kind: string) {
    connecting = kind;
    try { if (await openPlaidLink(null, kind)) reload(); } catch (err) { toast.error((err as Error).message); }
    connecting = "";
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>Plaid</Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#await status}
      <p class="text-sm text-muted-foreground">Loading…</p>
    {:then st}
      {#if st.items.length}
        <div class="flex flex-col gap-3">
          {#each st.items as it (it.item_id)}<PlaidItemRow {it} items={st.items} {accounts} />{/each}
        </div>
      {/if}
      <div class="flex flex-wrap gap-2">
        {#each [["bank", "Connect a bank or card"], ["investments", "Connect an investment account"]] as [k, label] (k)}
          <Button variant={k === "bank" ? "default" : "outline"} disabled={!st.configured || !!connecting}
            title={st.configured ? undefined : "Add your Plaid client ID and secret first"} onclick={() => connect(k)}>{label}</Button>
        {/each}
      </div>

      <details open={!st.configured}>
        <summary class="cursor-pointer py-1 text-sm text-muted-foreground">
          {st.configured ? `Keys · saved · ${st.env === "sandbox" ? "Sandbox" : "Production"}` : "Keys"}</summary>
        <div class="mt-2 flex flex-col gap-3">
          <p class={helpCls}>From Developers → Keys at <a class={linkCls} href="https://dashboard.plaid.com" target="_blank" rel="noopener">dashboard.plaid.com</a>.</p>
          <div class={rowCls}>
            <label class={`${fieldCls} w-full sm:w-64`}>Environment
              <select class={selectCls} bind:value={env} use:autosave={() => saveKeys(env, clientId)}>
                <option value="production">Production (your real accounts)</option><option value="sandbox">Sandbox (test data)</option>
              </select>
            </label>
            <label class={`${fieldCls} w-full sm:w-56`}>Client ID
              <input class={inputCls} bind:value={clientId} autocomplete="off" spellcheck="false" use:autosave={() => saveKeys(env, clientId)} /></label>
            <label class={`${fieldCls} w-full sm:w-56`}>Secret
              <input class={inputCls} type="password" autocomplete="off" placeholder={st.configured ? "•••••••• saved" : "secret"} use:autosave={saveSecret} /></label>
          </div>
          {#if st.redirect_uri}
            <p class={helpCls}>In the Plaid Dashboard, add <code class="rounded bg-muted px-1 text-foreground">{st.redirect_uri}</code> under Allowed redirect URIs
              (for banks like Chase that sign you in on their own site).</p>
          {/if}
        </div>
      </details>
      {#if plaidSession.last}
        {@const l = plaidSession.last}
        <p class={helpCls}>Last Link attempt ({l.at}): Link Session ID <code class="rounded bg-muted px-1 text-foreground select-all">{l.sid}</code>{#if l.request}{" · Request ID "} <code class="rounded bg-muted px-1 text-foreground select-all">{l.request}</code>{/if}</p>
      {/if}
    {:catch err}
      <p class="text-sm text-muted-foreground">{err.message}</p>
    {/await}
  </Card.Content>
</Card.Root>
