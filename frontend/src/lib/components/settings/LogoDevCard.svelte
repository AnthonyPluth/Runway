<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import ProblemNote from "./ProblemNote.svelte";
  import SecretInput from "./SecretInput.svelte";
  import ServiceRow from "./ServiceRow.svelte";
  import { helpCls, linkCls, rowCls } from "./ui";
  import { act, errMsg } from "$lib/act";

  // Merchant logos through Logo.dev, for merchants Plaid has no logo for.
  const configured = $derived(!!app.state?.logodev_configured);

  // Where logos stand: how many Runway has, how many are still to fetch, and why Logo.dev last failed.
  interface Status { plaid: number; logodev: number; unknown: number; waiting: number; last_error: string | null; last_error_name: string | null; searchable: boolean; fetching: boolean }
  let st = $state<Status | null>(null);
  // When it can't be read, the last numbers don't stay up as if they were current: the card says so, with Retry.
  let statusError = $state("");
  const loadStatus = () => api<Status>("/api/logodev/status").then((r) => { st = r; statusError = ""; }).catch((err) => { st = null; statusError = errMsg(err); });
  loadStatus();
  async function fetchNow() {
    await act(async () => { await api("/api/logodev/fetch", { method: "POST" }); toast.success("Fetching logos now. They fill in over the next few minutes."); });
    setTimeout(loadStatus, 4000);
  }

  async function saveKey(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/logodev/settings", { method: "POST", body: { token: f.value } });
    f.value = "";
    toast.success("Key saved: fetching logos for the past year now. They fill in over the next few minutes."); await refreshState(); loadStatus();
  }
  async function saveSecret(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    if (!f.value.trim()) return;
    await api("/api/logodev/settings", { method: "POST", body: { secret: f.value } });
    f.value = "";
    toast.success("Key saved: looking merchants up again with Brand Search."); loadStatus();
  }
  async function clearSecret() {
    await act(async () => { await api("/api/logodev/settings", { method: "POST", body: { clear_secret: true } }); loadStatus(); });
  }
  async function clearKey() {
    await act(async () => { await api("/api/logodev/settings", { method: "POST", body: { clear: true } }); await refreshState(); loadStatus(); });
  }
</script>

<ServiceRow name="Merchant and bank logos" purpose="Logo.dev · logos for merchants Plaid has none for" on={configured}
  warn={configured && !!(st?.last_error || st?.last_error_name)}>
  <p class={helpCls}>Get a free <a class={linkCls} href="https://www.logo.dev" target="_blank" rel="noopener">Logo.dev publishable key</a>.</p>
  <div class={rowCls}>
    <SecretInput label="Publishable key" class="w-full sm:w-72" placeholder={configured ? "Key saved" : "pk_…"} save={saveKey} />
    {#if configured}<Button variant="link" onclick={clearKey}>Remove key</Button>{/if}
  </div>
  {#if configured}
    <div class={rowCls}>
      <SecretInput label="Secret key" optional class="w-full sm:w-72" placeholder={st?.searchable ? "Key saved" : "sk_…"} save={saveSecret} />
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
      {#if st.last_error || st.last_error_name}
        <ProblemNote class="mt-1" text={`Logo.dev lookups ${st.last_error && st.last_error_name ? "are" : `by ${st.last_error ? "website" : "name"} are`} failing.`}
          detail={[st.last_error && `By website: ${st.last_error}`, st.last_error_name && `By name: ${st.last_error_name}`].filter(Boolean).join("\n")} />
      {/if}
      {#if [st.last_error, st.last_error_name].some((e) => e && /40[13]/.test(e))}
        <p class="mt-1 text-muted-foreground">Logo.dev refused the key. Check that it's the publishable one (<code>pk_…</code>), and in
          Logo.dev's dashboard that it isn't limited to particular websites: Runway asks from its server, not from a web page.
          {#if st.last_error_name && !st.last_error}Lookups by website work, so your plan may not include lookups by name.{/if}</p>
      {/if}
      {#if st.waiting || st.unknown}
        <Button variant="outline" size="sm" class="mt-2" disabled={st.fetching} onclick={fetchNow}>{st.fetching ? "Fetching…" : st.waiting ? "Fetch them now" : "Look them up again"}</Button>
      {/if}
    </div>
  {:else if statusError}
    <p class="text-sm text-muted-foreground">Couldn’t load the logo status: {statusError}
      <Button variant="link" class="h-auto p-0" onclick={loadStatus}>Retry</Button></p>
  {/if}
</ServiceRow>
