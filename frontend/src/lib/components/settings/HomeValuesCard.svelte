<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import SecretInput from "./SecretInput.svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import { helpCls, linkCls, rowCls } from "./ui";

  // Home values through Realie. The provider has changed before (it was RentCast), so everything about it lives here.
  const configured = $derived(!!app.state?.realie_configured);

  async function saveKey(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/realie/settings", { method: "POST", body: { api_key: f.value } });
    f.value = "";
    toast.success("Key saved"); await refreshState();
  }
  async function clearKey() {
    try { await api("/api/realie/settings", { method: "POST", body: { clear: true } }); await refreshState(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<ServiceRow name="Home values" purpose="Realie · keeps each home’s value current" on={configured}>
  <p class={helpCls}>Get a free <a class={linkCls} href="https://www.realie.ai/real-estate-data-api" target="_blank" rel="noopener">Realie key</a>.</p>
  <div class={rowCls}>
    <SecretInput label="Realie API key" class="w-full sm:w-72" placeholder={configured ? "Key saved" : "paste your key"} save={saveKey} />
    {#if configured}<Button variant="link" onclick={clearKey}>Remove key</Button>{/if}
  </div>
</ServiceRow>
