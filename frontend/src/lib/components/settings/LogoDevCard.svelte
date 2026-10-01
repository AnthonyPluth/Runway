<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import ServiceRow from "./ServiceRow.svelte";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls } from "./ui";

  // Merchant logos through Logo.dev, for merchants Plaid has no logo for.
  const configured = $derived(!!app.state?.logodev_configured);

  // Where logos stand: how many Runway has, how many are still to fetch, and why Logo.dev last failed.
  interface Status { plaid: number; logodev: number; unknown: number; waiting: number; last_error: string | null; last_error_name: string | null; searchable: boolean; fetching: boolean }
  let st = $state<Status | null>(null);
  const loadStatus = () => api<Status>("/api/logodev/status").then((r) => (st = r)).catch(() => {});
  loadStatus();
  async function fetchNow() {
    try { await api("/api/logodev/fetch", { method: "POST" }); toast.success("Fetching logos now. They fill in over the next few minutes."); }
    catch (err) { toast.error((err as Error).message); }
    setTimeout(loadStatus, 4000);
  }

  async function saveKey(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/logodev/settings", { method: "POST", body: { token: f.value } });
    toast.success("Logo.dev key saved: fetching logos for the past year now. They fill in over the next few minutes."); await refreshState(); reload();
  }
  async function saveSecret(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/logodev/settings", { method: "POST", body: { secret: f.value } });
    toast.success("Secret key saved: looking merchants up again with Brand Search."); f.value = ""; loadStatus();
  }
  async function clearSecret() {
    try { await api("/api/logodev/settings", { method: "POST", body: { clear_secret: true } }); loadStatus(); }
    catch (err) { toast.error((err as Error).message); }
  }
  async function clearKey() {
    try { await api("/api/logodev/settings", { method: "POST", body: { clear: true } }); await refreshState(); reload(); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

<ServiceRow name="Merchant and bank logos" purpose="Logo.dev · logos for merchants Plaid has none for" on={configured}>
  <p class={helpCls}>Get a free <a class={linkCls} href="https://www.logo.dev" target="_blank" rel="noopener">Logo.dev publishable key</a>.</p>
  <div class={rowCls}>
    <label class={`${fieldCls} w-full sm:w-72`}>Publishable key
      <input class={inputCls} type="password" autocomplete="off" placeholder={configured ? "•••••••• saved" : "pk_…"} use:autosave={saveKey} /></label>
    {#if configured}<Button variant="link" onclick={clearKey}>Remove key</Button>{/if}
  </div>
  {#if configured}
    <div class={rowCls}>
      <label class={`${fieldCls} w-full sm:w-72`}>Secret key <span class="font-normal text-muted-foreground">(optional)</span>
        <input class={inputCls} type="password" autocomplete="off" placeholder={st?.searchable ? "•••••••• saved" : "sk_…"} use:autosave={saveSecret} /></label>
      {#if st?.searchable}<Button variant="link" onclick={clearSecret}>Remove secret key</Button>{/if}
    </div>
  {/if}
  {#if st}
    <div class="rounded-lg bg-muted/50 p-3 text-sm">
      <p class="tabular-nums">
        <b class="font-medium">{st.plaid + st.logodev}</b> merchants have a logo ({st.plaid} from Plaid, {st.logodev} from Logo.dev)
        {#if st.unknown} · {st.unknown} Logo.dev doesn't know{/if}
        {#if st.waiting} · {st.waiting} waiting to be fetched{/if}
      </p>
      {#if !configured && !st.plaid}
        <p class="mt-1 text-muted-foreground">No logos yet: Plaid hasn't sent any, and there's no Logo.dev key. Add one above to get logos for most merchants.</p>
      {/if}
      {#if st.last_error}<p class="mt-1 text-destructive">Looking up by website: {st.last_error}</p>{/if}
      {#if st.last_error_name}<p class="mt-1 text-destructive">Looking up by name: {st.last_error_name}</p>{/if}
      {#if [st.last_error, st.last_error_name].some((e) => e && /40[13]/.test(e))}
        <p class="mt-1 text-muted-foreground">Logo.dev refused the key. Check that it's the publishable one (<code>pk_…</code>), and in
          Logo.dev's dashboard that it isn't limited to particular websites: Runway asks from its server, not from a web page.
          {#if st.last_error_name && !st.last_error}Lookups by website work, so your plan may not include lookups by name.{/if}</p>
      {/if}
      {#if st.waiting || st.unknown}
        <Button variant="outline" size="sm" class="mt-2" disabled={st.fetching} onclick={fetchNow}>{st.fetching ? "Fetching…" : st.waiting ? "Fetch them now" : "Look them up again"}</Button>
      {/if}
    </div>
  {/if}
</ServiceRow>
