<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import { fmtDate } from "$lib/format";
  import { toast } from "svelte-sonner";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import type { CartaStatus } from "./types";
  import { fieldCls, inputCls, linkCls, rowCls, selectCls } from "./ui";

  // Carta (stock options, RSUs and shares) as a row of the browser extension's list: read by the extension, or through
  // Carta's Portfolio API (which needs Carta to approve your app), or Carta's sample data.
  let c = $state<CartaStatus | null>(null);
  let env = $state("production");
  let clientId = $state("");
  let secret = $state("");
  let syncing = $state(false);
  const redirect = `${location.origin}/carta/callback`;

  async function load() {
    c = (await api<{ carta: CartaStatus }>("/api/equity")).carta;
    env = c.env; clientId = c.client_id || ""; secret = "";
  }
  load().catch((err) => toast.error((err as Error).message));

  const save = () => api("/api/carta/settings", { method: "POST", body: { env, client_id: clientId, client_secret: secret, origin: location.origin } });
  async function changeEnv() { try { await save(); await load(); } catch (err) { toast.error((err as Error).message); } }
  async function saveField() { await save(); toast.success("Saved"); }
  async function connect() {
    try { await save(); const r = await api<{ url: string }>("/api/carta/connect", { method: "POST", body: { origin: location.origin } }); location.href = r.url; }
    catch (err) { toast.error((err as Error).message); }
  }
  async function sync() {
    syncing = true;
    try {
      const r = await api<{ companies: number; grants: number }>("/api/carta/sync", { method: "POST" });
      toast.success(`Carta: ${r.companies} compan${r.companies === 1 ? "y" : "ies"}, ${r.grants} grant${r.grants === 1 ? "" : "s"}`);
    } catch (err) { toast.error((err as Error).message); }
    syncing = false;
    load();
  }
  async function disconnect() {
    try { await api("/api/carta/disconnect", { method: "POST" }); toast.success("Disconnected"); load(); }
    catch (err) { toast.error((err as Error).message); }
  }
  const code = "rounded bg-muted px-1 text-foreground";
</script>

{#if c}
  <div class="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-0.5 border-b py-1.5 last:border-b-0">
    <b class="text-sm">Carta</b>
    <span class="text-xs text-muted-foreground">
      {c.web_last ? `last read ${fmtDate(c.web_last)}` : "not read yet"}
      {#if c.web_capture}{" · "}<a class={linkCls} href="/api/carta/capture" download>Download what the extension read</a> (to see why something's missing){/if}
    </span>
  </div>
  {#if c.web_error}<Alert.Root class="my-2"><TriangleAlert /><Alert.Description><p>{c.web_error}</p></Alert.Description></Alert.Root>{/if}
  <details class="group mt-1" open={c.connected || !!c.client_id}>
    <summary class="flex cursor-pointer list-none items-center gap-1.5 py-1 text-sm text-muted-foreground select-none [&::-webkit-details-marker]:hidden"
      title="Carta's Portfolio API works only for apps Carta approves. An app made in Carta's developer portal is a Playground app (Carta's test environment, dummy data) until Carta grants it production access. Carta's sample data works without any of that, to see how it looks.">
      <ChevronRight class="size-4 shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Carta's API instead (needs Carta to approve your app)</summary>
    <div class="mt-2 flex flex-col gap-3">
      <div class={rowCls}>
        <label class={`${fieldCls} w-full sm:w-72`}>Environment
          <select class={selectCls} bind:value={env} onchange={changeEnv}>
            <option value="production">Carta (your real account)</option>
            <option value="playground">Carta Playground (developer portal test app)</option>
            <option value="mock">Carta's sample data</option>
          </select>
        </label>
        {#if env !== "mock"}
          <span class={`${fieldCls} w-full sm:w-56`}>Redirect URI<code class={`${code} h-9 content-center truncate`} title="Register this as the app's redirect URI in Carta">{redirect}</code></span>
          <label class={`${fieldCls} w-full sm:w-56`}>Client id
            <input class={inputCls} bind:value={clientId} autocomplete="off" spellcheck="false" use:autosave={saveField} /></label>
          <label class={`${fieldCls} w-full sm:w-56`}>Client secret
            <input class={inputCls} type="password" bind:value={secret} placeholder={c.has_secret ? "•••••••• saved" : ""} autocomplete="off" use:autosave={saveField} /></label>
        {/if}
      </div>
      <div class="flex flex-wrap items-center gap-2 text-sm">
        {#if c.connected}
          <span>Connected{c.last_sync ? ` · last read ${fmtDate(c.last_sync)}` : ""}</span>
          <Button variant="outline" size="sm" disabled={syncing} onclick={sync}>{syncing ? "Reading…" : "Sync now"}</Button>
          <Button variant="link" size="sm" onclick={disconnect}>Disconnect</Button>
        {:else}
          <Button variant="outline" onclick={connect}>Connect Carta's API</Button>
        {/if}
      </div>
      {#if c.last_error}<Alert.Root variant="destructive"><TriangleAlert /><Alert.Description><p>{c.last_error}</p></Alert.Description></Alert.Root>{/if}
    </div>
  </details>
{/if}
