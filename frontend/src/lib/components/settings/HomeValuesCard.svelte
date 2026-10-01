<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import ServiceRow from "./ServiceRow.svelte";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls } from "./ui";

  // Home values through Realie. The provider has changed before (it was RentCast), so everything about it lives here.
  const configured = $derived(!!app.state?.realie_configured);

  async function saveKey(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/realie/settings", { method: "POST", body: { api_key: f.value } });
    toast.success("Realie key saved"); await refreshState(); reload();
  }
  async function clearKey() {
    try { await api("/api/realie/settings", { method: "POST", body: { clear: true } }); await refreshState(); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<ServiceRow name="Home values" purpose="Realie · keeps each home’s value current" on={configured}>
  <p class={helpCls}>Get a free <a class={linkCls} href="https://www.realie.ai/real-estate-data-api" target="_blank" rel="noopener">Realie key</a>.</p>
  <div class={rowCls}>
    <label class={`${fieldCls} w-full sm:w-72`}>Realie API key
      <input class={inputCls} type="password" autocomplete="off" placeholder={configured ? "•••••••• saved" : "paste your key"} use:autosave={saveKey} /></label>
    {#if configured}<Button variant="link" onclick={clearKey}>Remove key</Button>{/if}
  </div>
</ServiceRow>
