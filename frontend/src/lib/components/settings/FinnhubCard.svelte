<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import ServiceRow from "./ServiceRow.svelte";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls } from "./ui";

  // Real-time stock prices through Finnhub. Runway keeps one connection to it on the server, so the key never reaches
  // your browser. Without a key, live prices come from Yahoo, as before.
  const configured = $derived(!!app.state?.finnhub_configured);

  // The connection only runs while prices are being streamed (the Investments page open with the market open).
  interface Status { configured: boolean; active: boolean; connected: boolean; symbols: number; limit: number; error: string | null }
  let st = $state<Status | null>(null);
  const loadStatus = () => api<Status>("/api/finnhub/status").then((r) => (st = r)).catch(() => {});
  loadStatus();

  // Checked with one quote before it's saved, so a mistyped key is caught here (the error shows under the field).
  async function saveKey(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/finnhub/settings", { method: "POST", body: { api_key: f.value } });
    toast.success("Finnhub key saved. Live prices use it the next time the market is open."); f.value = ""; await refreshState(); loadStatus();
  }
  async function clearKey() {
    try { await api("/api/finnhub/settings", { method: "POST", body: { clear: true } }); await refreshState(); loadStatus(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<ServiceRow name="Live stock prices" purpose="Finnhub · real-time trades on Investments" on={configured}>
  <p class={helpCls}>Get a free <a class={linkCls} href="https://finnhub.io/register" target="_blank" rel="noopener">Finnhub key</a>.</p>
  <div class={rowCls}>
    <label class={`${fieldCls} w-full sm:w-72`}>Finnhub API key
      <input class={inputCls} type="password" autocomplete="off" placeholder={configured ? "•••••••• saved" : "paste your key"} use:autosave={saveKey} /></label>
    {#if configured}<Button variant="link" onclick={clearKey}>Remove key</Button>{/if}
  </div>
  {#if configured && st}
    <div class="rounded-lg bg-muted/50 p-3 text-sm">
      {#if st.error}
        <p class="text-destructive">{st.error}</p>
      {:else if st.connected}
        <p><b class="font-medium">Connected.</b> <span class="tabular-nums">{st.symbols}</span> of {st.limit} tickers streaming.</p>
      {:else}
        <p class="text-muted-foreground">Not connected right now. Runway connects when the Investments page is open with the market open.</p>
      {/if}
      <Button variant="outline" size="sm" class="mt-2" onclick={loadStatus}>Refresh status</Button>
    </div>
  {/if}
</ServiceRow>
