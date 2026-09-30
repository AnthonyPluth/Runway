<script lang="ts">
  import { api } from "$lib/api";
  import { reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { toast } from "svelte-sonner";
  import type { PlaidStatus } from "./types";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls, selectCls } from "./ui";

  // The Plaid API keys, collapsed once they're set (`open` is bound so Connect a bank can open it when they aren't).
  let { st, open = $bindable(false) }: { st: PlaidStatus; open?: boolean } = $props();

  const saveKeys = (env: string, clientId: string) => api("/api/plaid/settings", { method: "POST", body: { env, client_id: clientId } });
  // svelte-ignore state_referenced_locally
  let env = $state(st.env === "sandbox" ? "sandbox" : "production");
  // svelte-ignore state_referenced_locally
  let clientId = $state(st.client_id);

  async function saveSecret(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/plaid/settings", { method: "POST", body: { secret: f.value } });
    toast.success("Plaid secret saved"); reload();
  }
</script>

<details bind:open id="plaid-keys" class="rounded-xl border px-3 py-2.5">
  <summary class="cursor-pointer py-1 text-sm text-muted-foreground">
    {st.configured ? `Plaid API keys · saved · ${st.env === "sandbox" ? "Sandbox" : "Production"}` : "Plaid API keys · not set"}</summary>
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
