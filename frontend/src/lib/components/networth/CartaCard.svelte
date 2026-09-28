<script lang="ts">
  import { api } from "$lib/api";
  import { fromAction } from "svelte/attachments";
  import { autosave } from "$lib/autosave";
  import * as Alert from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import { fmtDate } from "$lib/format";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { toast } from "svelte-sonner";
  import type { CartaSettings, Equity } from "./types";

  // Settings → Connections: Carta, read by Runway's browser extension, or through Carta's Portfolio API (access comes
  // from Carta), or its sample data. Lives with the Equity card it feeds; the Settings page shows it.
  let c = $state<CartaSettings | null>(null), error = $state("");
  let env = $state("production"), clientId = $state(""), secret = $state("");
  async function load() {
    try {
      c = (await api<Equity>("/api/equity")).carta;
      env = c.env; clientId = c.client_id ?? ""; secret = ""; error = "";
    } catch (err) { error = (err as Error).message; }
  }
  load();
  const redirect = `${location.origin}/carta/callback`;
  const save = () => api("/api/carta/settings", { method: "POST", body: { env, client_id: clientId, client_secret: secret, origin: location.origin } });
  async function changeEnv() { try { await save(); await load(); } catch (err) { toast.error((err as Error).message); } }
  const keep = fromAction(autosave, () => async () => { try { await save(); toast("Saved"); } catch (err) { toast.error((err as Error).message); } });
  async function connect() {
    try { await save(); const r = await api<{ url: string }>("/api/carta/connect", { method: "POST", body: { origin: location.origin } }); location.href = r.url; }
    catch (err) { toast.error((err as Error).message); }
  }
  let reading = $state(false);
  async function sync() {
    reading = true;
    try {
      const r = await api<{ companies: number; grants: number }>("/api/carta/sync", { method: "POST" });
      toast(`Carta: ${r.companies} compan${r.companies === 1 ? "y" : "ies"}, ${r.grants} grant${r.grants === 1 ? "" : "s"}`);
    } catch (err) { toast.error((err as Error).message); }
    reading = false;
    load();
  }
  async function disconnect() {
    try { await api("/api/carta/disconnect", { method: "POST" }); toast("Disconnected"); load(); } catch (err) { toast.error((err as Error).message); }
  }
</script>

<Card.Root>
  <Card.Header>
    <Card.Title>Carta <span class="text-sm font-normal text-muted-foreground">optional: stock options, RSUs and shares</span></Card.Title>
  </Card.Header>
  <Card.Content class="flex flex-col gap-3 text-sm">
    {#if error && !c}<p class="text-destructive">{error}</p>
    {:else if !c}<p class="text-muted-foreground">Loading…</p>
    {:else}
      <p class="text-muted-foreground">Runway's browser extension reads your equity from carta.com with the sign-in in your browser (the same extension
        as Amazon and Target, <a class="font-medium text-foreground underline underline-offset-4" href="#setup/connections">set up above</a>): click <b>Carta</b> in the extension. It reads only
        what Carta shows you, and sends it only to Runway.</p>
      <p>{c.web_last ? `Last read from Carta ${fmtDate(c.web_last)}.` : "Not read from Carta yet."}
        {#if c.web_capture}<a class="underline underline-offset-4" href="/api/carta/capture" download>Download what the extension read</a> (to see why something's missing).{/if}</p>
      {#if c.web_error}<Alert.Root><TriangleAlert /><Alert.Description>{c.web_error}</Alert.Description></Alert.Root>{/if}
      <details open={c.connected || !!c.client_id}>
        <summary class="cursor-pointer text-muted-foreground">Carta's API instead (needs Carta to approve your app)</summary>
        <p class="mt-2 text-muted-foreground">Carta's Portfolio API works only for apps Carta approves. An app made in Carta's developer portal is a
          <b>Playground</b> app (Carta's test environment, dummy data) until Carta grants it production access. Register
          <code class="rounded bg-muted px-1">{redirect}</code> as its redirect URI. <b>Carta's sample data</b> works without any of that, to see how it looks.</p>
        <div class="mt-3 flex flex-wrap items-end gap-3">
          <label class="flex flex-col gap-1">Environment
            <NativeSelect bind:value={env} onchange={changeEnv}>
              <option value="production">Carta (your real account)</option>
              <option value="playground">Carta Playground (developer portal test app)</option>
              <option value="mock">Carta's sample data</option>
            </NativeSelect>
          </label>
          {#if env !== "mock"}
            <label class="flex flex-col gap-1">Client id<Input class="w-56" bind:value={clientId} autocomplete="off" spellcheck="false" {@attach keep} /></label>
            <label class="flex flex-col gap-1">Client secret<Input class="w-56" type="password" bind:value={secret} autocomplete="off" placeholder={c.has_secret ? "•••••••• saved" : ""} {@attach keep} /></label>
          {/if}
        </div>
        <div class="mt-3 flex flex-wrap items-center gap-2">
          {#if c.connected}
            <span>Connected{c.last_sync ? ` · last read ${fmtDate(c.last_sync)}` : ""}</span>
            <Button variant="outline" size="sm" disabled={reading} onclick={sync}>{reading ? "Reading…" : "Sync now"}</Button>
            <Button variant="link" size="sm" onclick={disconnect}>Disconnect</Button>
          {:else}
            <Button variant="outline" size="sm" onclick={connect}>Connect Carta's API</Button>
          {/if}
        </div>
        {#if c.last_error}
          <Alert.Root variant="destructive" class="mt-3"><TriangleAlert /><Alert.Description>{c.last_error}</Alert.Description></Alert.Root>
        {/if}
      </details>
    {/if}
  </Card.Content>
</Card.Root>
