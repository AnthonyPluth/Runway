<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import SecretInput from "./SecretInput.svelte";
  import type { PlaidStatus } from "./types";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls, selectCls } from "./ui";
  import { act } from "$lib/act";

  // The Plaid API keys, collapsed once they're set (`open` is bound so Connect a bank can open it when they aren't).
  // They're a pair, so they're saved together with Save rather than field by field; the secret field is emptied once
  // it's saved (the server never sends it back).
  let { st, open = $bindable(false) }: { st: PlaidStatus; open?: boolean } = $props();

  // svelte-ignore state_referenced_locally
  let env = $state(st.env === "sandbox" ? "sandbox" : "production");
  // svelte-ignore state_referenced_locally
  let clientId = $state(st.client_id);
  let secret = $state("");
  let saving = $state(false);
  // svelte-ignore state_referenced_locally
  const savedEnv = st.env === "sandbox" ? "sandbox" : "production";
  const changed = $derived(env !== savedEnv || clientId.trim() !== st.client_id || !!secret.trim());

  async function save(e: SubmitEvent) {
    e.preventDefault();
    if (!clientId.trim()) { toast.error("Enter the client ID."); return; }
    if (!st.configured && !secret.trim()) { toast.error("Enter the secret."); return; }
    await act(async () => {
      await api("/api/plaid/settings", { method: "POST", body: { env, client_id: clientId.trim(), ...(secret.trim() ? { secret: secret.trim() } : {}) } });
      secret = "";
      toast.success("Plaid keys saved");
      reload();
    }, { busy: (on) => (saving = on) });
  }
</script>

<details bind:open id="plaid-keys" class="rounded-xl border px-3 py-2.5">
  <summary class="cursor-pointer py-1 text-sm text-muted-foreground">
    {st.configured ? `Plaid API keys · Keys saved · ${st.env === "sandbox" ? "Sandbox" : "Production"}` : "Plaid API keys · not set"}</summary>
  <form class="mt-2 flex flex-col gap-3" aria-label="Plaid API keys" onsubmit={save}>
    <p class={helpCls}>From Developers → Keys at <a class={linkCls} href="https://dashboard.plaid.com" target="_blank" rel="noopener">dashboard.plaid.com</a>.</p>
    <div class={rowCls}>
      <label class={`${fieldCls} w-full sm:w-64`}>Environment
        <select class={selectCls} bind:value={env}>
          <option value="production">Production (your real accounts)</option><option value="sandbox">Sandbox (test data)</option>
        </select>
      </label>
      <label class={`${fieldCls} w-full sm:w-56`}>Client ID
        <input class={inputCls} bind:value={clientId} autocomplete="off" spellcheck="false" /></label>
      <SecretInput label="Secret" class="w-full sm:w-56" bind:value={secret} placeholder={st.configured ? "Key saved" : "secret"} />
      <Button type="submit" disabled={saving || !changed}>{saving ? "Saving…" : "Save"}</Button>
    </div>
    {#if st.redirect_uri}
      <p class={helpCls}>In the Plaid Dashboard, add <code class="rounded bg-muted px-1 text-foreground">{st.redirect_uri}</code> under Allowed redirect URIs
        (for banks like Chase that sign you in on their own site).</p>
    {/if}
  </form>
</details>
