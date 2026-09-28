<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { toast } from "svelte-sonner";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls, titleNote } from "./ui";

  // Merchant logos through Logo.dev, for merchants Plaid has no logo for.
  const configured = $derived(!!app.state?.logodev_configured);

  async function saveKey(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/logodev/settings", { method: "POST", body: { token: f.value } });
    toast.success("Logo.dev key saved: logos arrive with the next sync"); await refreshState(); reload();
  }
  async function clearKey() {
    try { await api("/api/logodev/settings", { method: "POST", body: { clear: true } }); await refreshState(); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>Merchant logos <span class={titleNote}>optional, via Logo.dev</span></Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    <p class={helpCls}>Plaid has logos for many merchants. For the rest, a free <a class={linkCls} href="https://www.logo.dev" target="_blank" rel="noopener">Logo.dev</a>
      publishable key lets Runway fetch one by the merchant's website during each sync. Runway downloads and serves them itself, so
      your browser never contacts Logo.dev, and Logo.dev only sees merchants' websites.</p>
    <div class={rowCls}>
      <label class={`${fieldCls} w-full sm:w-72`}>Publishable key
        <input class={inputCls} type="password" autocomplete="off" placeholder={configured ? "•••••••• saved" : "pk_…"} use:autosave={saveKey} /></label>
      {#if configured}<Button variant="link" onclick={clearKey}>Remove key</Button>{/if}
    </div>
  </Card.Content>
</Card.Root>
