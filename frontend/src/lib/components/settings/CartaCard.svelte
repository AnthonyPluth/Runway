<script lang="ts">
  import { api } from "$lib/api";
  import { autosave } from "$lib/autosave";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmtDate } from "$lib/format";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import type { CartaStatus } from "./types";
  import { fieldCls, helpCls, inputCls, linkCls, rowCls, selectCls, titleNote } from "./ui";

  // Carta (stock options, RSUs and shares): read by the browser extension, or through Carta's Portfolio API (which
  // needs Carta to approve your app), or Carta's sample data.
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

<Card.Root>
  <Card.Header><Card.Title>Carta{#if c}<span class={titleNote}>optional: stock options, RSUs and shares</span>{/if}</Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#if !c}
      <p class="text-sm text-muted-foreground">Loading…</p>
    {:else}
      <p class={helpCls}>Runway's browser extension reads your equity from carta.com with the sign-in in your browser (the same extension
        as Amazon and Target, <a class={linkCls} href="#setup/connections">set up above</a>): click <b class="text-foreground">Carta</b> in the extension. It reads only
        what Carta shows you, and sends it only to Runway.</p>
      <p class="text-sm">
        {c.web_last ? `Last read from Carta ${fmtDate(c.web_last)}.` : "Not read from Carta yet."}
        {#if c.web_capture}<a class={linkCls} href="/api/carta/capture" download>Download what the extension read</a> (to see why something's missing).{/if}
      </p>
      {#if c.web_error}<Alert.Root><TriangleAlert /><Alert.Description><p>{c.web_error}</p></Alert.Description></Alert.Root>{/if}
      <details open={c.connected || !!c.client_id}>
        <summary class="cursor-pointer py-1 text-sm text-muted-foreground">Carta's API instead (needs Carta to approve your app)</summary>
        <div class="mt-2 flex flex-col gap-3">
          <p class={helpCls}>Carta's Portfolio API works only for apps Carta approves. An app made in Carta's developer portal is a
            <b class="text-foreground">Playground</b> app (Carta's test environment, dummy data) until Carta grants it production access. Register
            <code class={code}>{redirect}</code> as its redirect URI. <b class="text-foreground">Carta's sample data</b> works without any of that, to see how it looks.</p>
          <div class={rowCls}>
            <label class={`${fieldCls} w-full sm:w-72`}>Environment
              <select class={selectCls} bind:value={env} onchange={changeEnv}>
                <option value="production">Carta (your real account)</option>
                <option value="playground">Carta Playground (developer portal test app)</option>
                <option value="mock">Carta's sample data</option>
              </select>
            </label>
            {#if env !== "mock"}
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
  </Card.Content>
</Card.Root>
